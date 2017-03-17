import time
import humanize

from peewee import fn
from holster.emitter import Priority

from datetime import datetime

from disco.bot import CommandLevels
from disco.types.channel import Channel
from disco.types.message import MessageTable, MessageEmbed, MessageEmbedField, MessageEmbedThumbnail

from rowboat.plugins import RowboatPlugin as Plugin
from rowboat.util import C
from rowboat.util.eventual import Eventual
from rowboat.util.images import get_dominant_colors_user
from rowboat.redis import rdb
from rowboat.types import Field, ListField, snowflake, SlottedModel
from rowboat.types.plugin import PluginConfig
from rowboat.plugins.modlog import Actions
from rowboat.models.user import User, Infraction
from rowboat.models.guild import GuildMemberBackup
from rowboat.models.message import Message, MessageArchive


EMOJI_STATS_SQL = """
WITH emojis AS (
    SELECT jsonb_array_elements_text(emojis) as id
    FROM messages WHERE guild_id={gid} AND jsonb_array_length(emojis) > 0
)
SELECT gm.emoji_id, count(*), gm.name
FROM emojis
JOIN guildemojis gm ON gm.emoji_id=emojis.id::bigint
WHERE gm.guild_id={gid}
GROUP BY gm.emoji_id
{}
LIMIT 10;
"""


def maybe_string(obj, exists, notexists, **kwargs):
    if obj:
        return exists.format(o=obj, **kwargs)
    return notexists.format(**kwargs)


class PersistConfig(SlottedModel):
    roles = Field(bool, default=False)
    nickname = Field(bool, default=False)
    voice = Field(bool, default=False)

    role_ids = ListField(snowflake, default=[])


class AdminConfig(PluginConfig):
    confirm_actions = Field(bool, default=True)

    # Role saving information
    persist = Field(PersistConfig, default=None)

    # The mute role
    mute_role = Field(snowflake, default=None)
    temp_mute_role = Field(snowflake, default=None)


@Plugin.with_config(AdminConfig)
class AdminPlugin(Plugin):
    def load(self, ctx):
        super(AdminPlugin, self).load(ctx)

        self.inf_task = Eventual(self.clear_infractions)
        self.spawn(self.queue_infractions)

    def queue_infractions(self):
        time.sleep(5)

        next_infraction = list(Infraction.select().where(
            (Infraction.active == 1) &
            (~(Infraction.expires_at >> None))
        ).order_by(Infraction.expires_at.asc()).limit(1))

        if not next_infraction:
            self.log.info('No infractions to wait for')
            return

        self.log.info('Waiting until %s', next_infraction[0].expires_at)
        self.inf_task.set_next_schedule(next_infraction[0].expires_at)

    def clear_infractions(self):
        expired = list(Infraction.select().where(
            (Infraction.active == 1) &
            (Infraction.expires_at < datetime.utcnow())
        ))

        for item in expired:
            guild = self.state.guilds.get(item.guild_id)
            if not guild:
                continue

            # TODO: hacky
            type_ = {i.index: i for i in Infraction.Types.attrs}[item.type_]
            if type_ == Infraction.Types.TEMPBAN:
                # TODO: debounce
                guild.delete_ban(item.user_id)
            elif type_ == Infraction.Types.TEMPMUTE:
                # TODO: remove in backups
                member = guild.get_member(item.user_id)
                if member and item.metadata['role'] in member.roles:
                    member.remove_role(item.metadata['role'])

            # TODO: n+1
            item.active = False
            item.save()

        self.queue_infractions()

    @Plugin.command('roles', level=CommandLevels.MOD)
    def roles(self, event):
        roles = []
        for role in event.guild.roles.values():
            roles.append(C(u'{} - {}'.format(role.id, role.name)))
        return event.msg.reply(u'```{}```'.format('\n'.join(roles)))

    @Plugin.listen('GuildMemberRemove', priority=Priority.BEFORE)
    def on_guild_member_remove(self, event):
        GuildMemberBackup.create_from_member(event.guild.members.get(event.user.id))

    @Plugin.listen('GuildMemberAdd')
    def on_guild_member_add(self, event):
        if not event.config.persist:
            return

        try:
            backup = GuildMemberBackup.get(guild_id=event.guild_id, user_id=event.user.id)
        except GuildMemberBackup.DoesNotExist:
            return

        kwargs = {}

        if event.config.persist.roles:
            roles = set(event.guild.roles.keys())

            if event.config.persist.role_ids:
                roles &= set(event.config.persist.role_ids)

            kwargs['roles'] = list(roles)

        if event.config.persist.nickname and backup.nick is not None:
            kwargs['nick'] = backup.nick

        if event.config.persist.voice:
            kwargs['mute'] = backup.mute
            kwargs['deaf'] = backup.deaf

        if not kwargs:
            return

        self.bot.plugins.get('ModLogPlugin').create_debounce(event, event.member.user, 'restore')
        event.member.modify(**kwargs)
        self.bot.plugins.get('ModLogPlugin').log_action_ext(Actions.MEMBER_RESTORE, event)

    @Plugin.command('mute', '<user:user|snowflake> [reason:str...]', level=CommandLevels.MOD)
    def mute(self, event, user, reason=None):
        member = event.guild.get_member(user)
        if member:
            if not event.config.mute_role:
                event.msg.reply(':warning: mute is not setup on this server')
                return

            if len({event.config.temp_mute_role, event.config.mute_role} & set(member.roles)):
                event.msg.reply(':warning: {} is already muted'.format(member.user))
                return

            Infraction.mute(self, event, member, reason)
            if event.config.confirm_actions:
                event.msg.reply(maybe_string(
                    reason,
                    u':ok_hand: {u} is now muted (`{o}`)',
                    u':ok_hand: {u} is now muted',
                    u=member.user,
                ))
        else:
            event.msg.reply(':warning: Invalid user!')

    @Plugin.command('tempmute', '<user:user|snowflake> <duration:duration> [reason:str...]', level=CommandLevels.MOD)
    def tempmute(self, event, user, duration, reason=None):
        member = event.guild.get_member(user)
        if member:
            if not event.config.temp_mute_role and not event.config.mute_role:
                event.msg.reply(':warning: mute is not setup on this server')
                return

            if len({event.config.temp_mute_role, event.config.mute_role} & set(member.roles)):
                event.msg.reply(':warning: {} is already muted'.format(member.user))
                return

            duration = datetime.utcnow() + (datetime.utcnow() - duration)
            self.inf_task.set_next_schedule(duration)
            Infraction.tempmute(self, event, member, reason, duration)
            if event.config.confirm_actions:
                event.msg.reply(maybe_string(
                    reason,
                    u':ok_hand: {u} is now muted until {t} (`{o}`)',
                    u':ok_hand: {u} is now muted until {t}',
                    u=member.user,
                    t=humanize.naturaltime(duration),
                ))
        else:
            event.msg.reply(':warning: Invalid user!')

    @Plugin.command('unmute', '<user:user|snowflake>', level=CommandLevels.MOD)
    def unmute(self, event, user, reason=None):
        # TOOD: eventually we should pull the role from the GuildMemberBackup if they arent in server
        member = event.guild.get_member(user)

        if member:
            if not event.config.temp_mute_role and not event.config.mute_role:
                event.msg.reply(':warning: mute is not setup on this server')
                return

            roles = {event.config.temp_mute_role, event.config.mute_role} & set(member.roles)
            if not len(roles):
                event.msg.reply(':warning: {} is not muted'.format(member.user))
                return

            Infraction.update(
                active=False
            ).where(
                (Infraction.guild_id == event.guild.id) &
                (Infraction.user_id == member.user.id) &
                (Infraction.type_ == Infraction.Types.TEMPMUTE) &
                (Infraction.active == 1)
            ).execute()

            self.bot.plugins.get('ModLogPlugin').create_debounce(event, member.user, 'unmuted', actor=unicode(event.author), roles=roles)

            for role in roles:
                member.remove_role(role)

            if event.config.confirm_actions:
                event.msg.reply(u':ok_hand: {} is now unmuted'.format(member.user))
        else:
            event.msg.reply(':warning: Invalid user!')

    @Plugin.command('kick', '<user:user|snowflake> [reason:str...]', level=CommandLevels.MOD)
    def kick(self, event, user, reason=None):
        """
        Kick a user from the server (with an optional reason for the modlog).
        """
        member = event.guild.get_member(user)
        if member:
            Infraction.kick(self, event, member, reason)
            if event.config.confirm_:
                event.msg.reply(maybe_string(
                    reason,
                    u':ok_hand: kicked {u} (`{r}`)',
                    u':ok_hand: kicked {u}',
                    u=member.user,
                ))
                event.msg.reply(u':ok_hand: kicked {} for `{}`'.format(user, reason or 'no reason given'))
        else:
            event.msg.reply(':warning: Invalid user!')

    @Plugin.command('ban', '<user:user|snowflake> [reason:str...]', level=CommandLevels.MOD)
    @Plugin.command('forceban', '<user:snowflake> [reason:str...]', level=CommandLevels.MOD)
    def ban(self, event, user, reason=None):
        """
        Ban a user from the server (with an optional reason for the modlog).
        """

        if isinstance(user, (int, long)):
            Infraction.ban(self, event, user, reason, guild=event.guild)
        else:
            member = event.guild.get_member(user)
            if member:
                Infraction.ban(self, event, member, reason, guild=event.guild)
            else:
                event.msg.reply(':warning: Invalid user!')
                return

        if event.config.config.confirm_actions:
            event.msg.reply(maybe_string(
                reason,
                u':ok_hand: banned {u} (`{r}`)',
                u':ok_hand: banned {u}',
                u=member.user,
            ))

    @Plugin.command('softban', '<user:user|snowflake> [reason:str...]', level=CommandLevels.MOD)
    def softban(self, event, user, reason=None):
        """
        Ban then unban a user from the server (with an optional reason for the modlog).
        """

        member = event.guild.get_member(user)
        if member:
            Infraction.softban(self, event, member, reason)
            if event.config.confirm_actions:
                event.msg.reply(maybe_string(
                    reason,
                    u':ok_hand: soft-banned {u} (`{r}`)',
                    u':ok_hand: soft-banned {u}',
                    u=member.user,
                ))
        else:
            event.msg.reply(':warning: Invalid user!')

    @Plugin.command('tempban', '<user:user|snowflake> <duration:duration> [reason:str...]', level=CommandLevels.MOD)
    def tempban(self, event, duration, user, reason=None):
        """
        Ban a user from the server for a given duration (with an optional reason for the modlog).
        """

        member = event.guild.get_member(user)
        if member:
            duration = datetime.utcnow() + (datetime.utcnow() - duration)
            self.inf_task.set_next_schedule(duration)
            Infraction.tempban(self, event, member, reason, duration)
            if event.config.confirm_actions:
                event.msg.reply(maybe_string(
                    reason,
                    u':ok_hand: temp-banned {u} until {t} (`{r}`)',
                    u':ok_hand: soft-banned {u} until {t}',
                    u=member.user,
                    t=humanize.naturaltime(duration),
                ))
        else:
            event.msg.reply(':warning: Invalid user!')

    @Plugin.command('archive here', '[size:int]', level=CommandLevels.MOD, context={'mode': 'all'})
    @Plugin.command('archive all', '[size:int]', level=CommandLevels.MOD, context={'mode': 'all'})
    @Plugin.command('archive user', '<user:user|snowflake> [size:int]', level=CommandLevels.MOD, context={'mode': 'user'})
    @Plugin.command('archive channel', '<channel:channel|snowflake> [size:int]', level=CommandLevels.MOD, context={'mode': 'channel'})
    def archive(self, event, size=50, mode=None, user=None, channel=None):
        if 0 > size >= 15000:
            return event.msg.reply(':warning: Too many messages, must be between 1-15000')

        q = Message.select(Message.id).join(User).order_by(Message.id.desc()).limit(size)

        if mode in ('all', 'channel'):
            q = q.where((Message.channel_id == (channel or event.channel).id))
        else:
            q = q.where(
                (Message.author_id == (user if isinstance(user, (int, long)) else user.id)) &
                (Message.guild_id == event.guild.id)
            )

        archive = MessageArchive.create_from_message_ids([i.id for i in q])
        event.msg.reply('OK, archived {} messages at {}'.format(len(archive.message_ids), archive.url))

    @Plugin.command('clean all', '[size:int]', level=CommandLevels.MOD, context={'mode': 'all'})
    @Plugin.command('clean bots', '[size:int]', level=CommandLevels.MOD, context={'mode': 'bots'})
    @Plugin.command('clean user', '<user:user> [size:int]', level=CommandLevels.MOD, context={'mode': 'user'})
    def clean(self, event, user=None, size=25, typ=None, mode='all'):
        if 0 > size >= 10000:
            return event.msg.reply(':warning: Too many messages, must be between 1-10000')

        lock = rdb.lock('clean-{}'.format(event.channel.id))
        if not lock.acquire(blocking=False):
            return event.msg.reply(':warning: already running a clean on this channel')

        try:
            query = Message.select().where(
                (Message.deleted >> False) &
                (Message.channel_id == event.channel.id)
            ).join(User).order_by(Message.timestamp.desc()).limit(size)

            if mode == 'bots':
                query = query.where((User.bot >> True))
            elif mode == 'user':
                query = query.where((User.user_id == user.id))

            msgs = list(reversed(query))
            event.channel.delete_messages(msgs)
            event.msg.reply(':wastebasket: Ok, deleted {} messages'.format(len(msgs))).after(5).delete()
        finally:
            lock.release()

    @Plugin.command('msgstats', '<user:user> [ctx:channel|snowflake|str]', level=CommandLevels.MOD)
    def msgstats(self, event, user, ctx=None):
        base_query = Message.select().where(
            (Message.author_id == user.id)
        )

        if ctx:
            if isinstance(ctx, Channel):
                base_query = base_query.where((Message.channel_id == ctx.id))
            elif isinstance(ctx, int):
                if ctx not in self.state.guilds:
                    return event.msg.reply(u':warning: unknown guild {}'.format(C(ctx)))
                base_query = base_query.where((Message.guild_id == ctx))
            elif ctx == 'channel':
                base_query = base_query.where((Message.channel_id == event.channel.id))
            elif ctx == 'guild':
                base_query = base_query.where((Message.guild_id == event.guild.id))
            else:
                return event.msg.reply(u':warning: invalid context {}'.format(C(ctx)))

        # Grab total messages/characters
        q = base_query.select(
            fn.Count('*'),
            fn.Sum(fn.char_length(Message.content)),
            fn.Sum(fn.jsonb_array_length(Message.emojis)),
        ).tuples()[0]

        emojis = list(Message.raw("""
            SELECT count(i)
            FROM (
                SELECT jsonb_array_elements(emojis)
                FROM messages WHERE author_id=%s
            ) i
            GROUP BY i
        """, (user.id, )).tuples())

        deleted = base_query.where((Message.deleted >> True)).count()

        embed = MessageEmbed()
        embed.fields.append(
            MessageEmbedField(name='Total Messages', value=q[0], inline=True))
        embed.fields.append(
            MessageEmbedField(name='Total Characters', value=q[1], inline=True))
        embed.fields.append(
            MessageEmbedField(name='Deleted Messages', value=deleted, inline=True))
        embed.fields.append(
            MessageEmbedField(name='Total Custom Emoji', value=sum(i[0] for i in emojis), inline=True))
        embed.fields.append(
            MessageEmbedField(name='Unique Emojis Used', value=len(emojis), inline=True))

        embed.thumbnail = MessageEmbedThumbnail(url=user.avatar_url)
        embed.color = get_dominant_colors_user(user)
        event.msg.reply('', embed=embed)

    @Plugin.command('emojistats most', level=CommandLevels.MOD, context={'mode': 'most'})
    @Plugin.command('emojistats least', level=CommandLevels.MOD, context={'mode': 'least'})
    def emojistats(self, event, mode='default'):
        if mode == 'most':
            sql = EMOJI_STATS_SQL.format('ORDER BY 2 DESC', gid=event.guild.id)
        else:
            sql = EMOJI_STATS_SQL.format('ORDER BY 2 ASC', gid=event.guild.id)

        q = list(Message.raw(sql).tuples())

        tbl = MessageTable()
        tbl.set_header('Count', 'Name', 'ID')

        for emoji_id, count, name in q:
            tbl.add(count, name, emoji_id)

        event.msg.reply(tbl.compile())
