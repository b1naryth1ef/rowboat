import re
import csv
import time
import gevent
import humanize
import operator

from StringIO import StringIO
from peewee import fn
from holster.emitter import Priority
from fuzzywuzzy import fuzz

from datetime import datetime, timedelta

from disco.bot import CommandLevels
from disco.types.user import User as DiscoUser
from disco.types.message import MessageTable, MessageEmbed, MessageEmbedField, MessageEmbedThumbnail
from disco.util.sanitize import S

from rowboat.plugins import RowboatPlugin as Plugin, CommandFail, CommandSuccess
from rowboat.util.timing import Eventual
from rowboat.util.images import get_dominant_colors_user
from rowboat.util.input import parse_duration
from rowboat.redis import rdb
from rowboat.types import Field, ListField, snowflake, SlottedModel
from rowboat.types.plugin import PluginConfig
from rowboat.plugins.modlog import Actions
from rowboat.models.user import User, Infraction
from rowboat.models.guild import GuildMemberBackup, GuildBan, GuildEmoji, GuildVoiceSession
from rowboat.models.message import Message, Reaction, MessageArchive

EMOJI_RE = re.compile(r'<:[a-zA-Z0-9_]+:([0-9]+)>')

B1NZY_USER_ID = 80351110224678912

CUSTOM_EMOJI_STATS_SERVER_SQL = """
SELECT gm.emoji_id, gm.name, count(*) FROM guildemojis gm
JOIN messages m ON m.emojis @> ARRAY[gm.emoji_id]
WHERE gm.deleted=false AND gm.guild_id={guild} AND m.guild_id={guild}
GROUP BY 1, 2
ORDER BY 3 {}
LIMIT 30
"""

CUSTOM_EMOJI_STATS_GLOBAL_SQL = """
SELECT gm.emoji_id, gm.name, count(*) FROM guildemojis gm
JOIN messages m ON m.emojis @> ARRAY[gm.emoji_id]
WHERE gm.deleted=false AND gm.guild_id={guild}
GROUP BY 1, 2
ORDER BY 3 {}
LIMIT 30
"""

GREEN_TICK_EMOJI_ID = 305231298799206401
RED_TICK_EMOJI_ID = 305231335512080385
GREEN_TICK_EMOJI = 'green_tick:305231298799206401'
RED_TICK_EMOJI = 'red_tick:305231335512080385'


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

    DONT_MENTION_B1NZY = Field(bool, default=False)


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
                member = guild.get_member(item.user_id)
                if member:
                    if item.metadata['role'] in member.roles:
                        member.remove_role(item.metadata['role'])
                else:
                    GuildMemberBackup.remove_role(
                        item.guild_id,
                        item.user_id,
                        item.metadata['role'])

            # TODO: n+1
            item.active = False
            item.save()

        self.queue_infractions()

    def restore_user(self, event, member):
        try:
            backup = GuildMemberBackup.get(guild_id=event.guild_id, user_id=member.user.id)
        except GuildMemberBackup.DoesNotExist:
            return

        kwargs = {}

        if event.config.persist.roles:
            roles = set(event.guild.roles.keys())

            if event.config.persist.role_ids:
                roles &= set(event.config.persist.role_ids)

            roles = set(backup.roles) & roles
            if roles:
                kwargs['roles'] = list(roles)

        if event.config.persist.nickname and backup.nick is not None:
            kwargs['nick'] = backup.nick

        if event.config.persist.voice and (backup.mute or backup.deaf):
            kwargs['mute'] = backup.mute
            kwargs['deaf'] = backup.deaf

        if not kwargs:
            return

        self.bot.plugins.get('ModLogPlugin').create_debounce(event, member.user.id, 'restore')
        member.modify(**kwargs)
        self.bot.plugins.get('ModLogPlugin').log_action_ext(Actions.MEMBER_RESTORE, event)

    @Plugin.listen('MessageCreate')
    def on_message_create(self, event):
        if not event.config.DONT_MENTION_B1NZY:
            return

        if B1NZY_USER_ID not in event.mentions:
            return

        member = event.guild.get_member(event.author)
        if not member or member.roles:
            return

        duration = datetime.utcnow() + timedelta(days=7)
        Infraction.tempban(self, event, member, 'AUTOBAN - mentioned b1nzy', duration)
        event.message.reply(u'{} pinged b1nzy for some reason, they are rip now...'.format(member))

    @Plugin.listen('GuildMemberRemove', priority=Priority.BEFORE)
    def on_guild_member_remove(self, event):
        if event.user.id in event.guild.members:
            GuildMemberBackup.create_from_member(event.guild.members.get(event.user.id))

    @Plugin.listen('GuildMemberAdd')
    def on_guild_member_add(self, event):
        if not event.config.persist:
            return

        self.restore_user(event, event.member)

    @Plugin.listen('GuildBanRemove')
    def on_guild_ban_remove(self, event):
        Infraction.update(
            active=False
        ).where(
            (Infraction.guild_id == event.guild.id) &
            (Infraction.user_id == event.user.id) &
            (Infraction.type_ == Infraction.Types.TEMPBAN) &
            (Infraction.active == 1)
        ).execute()

    @Plugin.command('unban', '<user:snowflake> [reason:str...]', level=CommandLevels.MOD)
    def unban(self, event, user, reason=None):
        try:
            GuildBan.get(user_id=user, guild_id=event.guild.id)
            event.guild.delete_ban(user)
        except GuildBan.DoesNotExist:
            raise CommandFail('user with id `{}` is not banned'.format(user))

        Infraction.create(
            guild_id=event.guild.id,
            user_id=user,
            actor_id=event.author.id,
            type_=Infraction.Types.UNBAN,
            reason=reason
        )
        raise CommandSuccess('unbanned user with id `{}`'.format(user))

    @Plugin.command('archive', group='infractions', level=CommandLevels.ADMIN)
    def infractions_archive(self, event):
        user = User.alias()
        actor = User.alias()

        q = Infraction.select(Infraction, user, actor).join(
            user,
            on=((Infraction.user_id == user.user_id).alias('user'))
        ).switch(Infraction).join(
            actor,
            on=((Infraction.actor_id == actor.user_id).alias('actor'))
        ).where(Infraction.guild_id == event.guild.id)

        buff = StringIO()
        w = csv.writer(buff)

        for inf in q:
            w.writerow([
                inf.id,
                inf.user_id,
                unicode(inf.user).encode('utf-8'),
                inf.actor_id,
                unicode(inf.actor).encode('utf-8'),
                unicode({i.index: i for i in Infraction.Types.attrs}[inf.type_]).encode('utf-8'),
                unicode(inf.reason).encode('utf-8'),
            ])

        event.msg.reply('Ok, here is an archive of all infractions', attachments=[
            ('infractions.csv', buff.getvalue())
        ])

    @Plugin.command('info', '<infraction:int>', group='infractions', level=CommandLevels.MOD)
    def infraction_info(self, event, infraction):
        try:
            user = User.alias()
            actor = User.alias()

            infraction = Infraction.select(Infraction, user, actor).join(
                user,
                on=((Infraction.user_id == user.user_id).alias('user'))
            ).switch(Infraction).join(
                actor,
                on=((Infraction.actor_id == actor.user_id).alias('actor'))
            ).where(
                    (Infraction.id == infraction) &
                    (Infraction.guild_id == event.guild.id)
            ).get()
        except Infraction.DoesNotExist:
            raise CommandFail('cannot find an infraction with ID `{}`'.format(infraction))

        type_ = {i.index: i for i in Infraction.Types.attrs}[infraction.type_]
        embed = MessageEmbed()

        if type_ in (Infraction.Types.MUTE, Infraction.Types.TEMPMUTE):
            embed.color = 0xfdfd96
        elif type_ in (Infraction.Types.KICK, Infraction.Types.SOFTBAN):
            embed.color = 0xffb347
        else:
            embed.color = 0xff6961

        embed.title = str(type_).title()
        embed.set_thumbnail(url=infraction.user.get_avatar_url())
        embed.add_field(name='User', value=unicode(infraction.user), inline=True)
        embed.add_field(name='Moderator', value=unicode(infraction.actor), inline=True)
        embed.add_field(name='Active', value='yes' if infraction.active else 'no', inline=True)
        if infraction.active and infraction.expires_at:
            embed.add_field(name='Expires', value=humanize.naturaldelta(infraction.expires_at - datetime.utcnow()))
        embed.add_field(name='Reason', value=infraction.reason or '_No Reason Given', inline=False)
        embed.timestamp = infraction.created_at.isoformat()
        event.msg.reply('', embed=embed)

    @Plugin.command('search', '[query:user|str...]', group='infractions', level=CommandLevels.MOD)
    def infraction_search(self, event, query=None):
        q = (Infraction.guild_id == event.guild.id)

        if query and isinstance(query, list) and isinstance(query[0], DiscoUser):
            query = query[0].id
        elif query:
            query = ' '.join(query)

        if query and (isinstance(query, int) or query.isdigit()):
            q &= (
                (Infraction.id == int(query)) |
                (Infraction.user_id == int(query)) |
                (Infraction.actor_id == int(query)))
        elif query:
            q &= (Infraction.reason ** query)

        user = User.alias()
        actor = User.alias()

        infractions = Infraction.select(Infraction, user, actor).join(
            user,
            on=((Infraction.user_id == user.user_id).alias('user'))
        ).switch(Infraction).join(
            actor,
            on=((Infraction.actor_id == actor.user_id).alias('actor'))
        ).where(q).order_by(Infraction.created_at.desc()).limit(6)

        tbl = MessageTable()

        tbl.set_header('ID', 'Created', 'Type', 'User', 'Moderator', 'Active', 'Reason')
        for inf in infractions:
            type_ = {i.index: i for i in Infraction.Types.attrs}[inf.type_]
            reason = inf.reason or ''
            if len(reason) > 256:
                reason = reason[:256] + '...'

            if inf.active:
                active = 'yes'
                if inf.expires_at:
                    active += ' (expires in {})'.format(humanize.naturaldelta(inf.expires_at - datetime.utcnow()))
            else:
                active = 'no'

            tbl.add(inf.id, inf.created_at.isoformat(), str(type_), unicode(inf.user), unicode(inf.actor), active, reason)

        event.msg.reply(tbl.compile())

    @Plugin.command('recent', aliases=['latest'], group='infractions', level=CommandLevels.MOD)
    def infractions_recent(self, event):
        # TODO: fucking write this bruh
        pass

    @Plugin.command('duration', '<infraction:int> <duration:str>', group='infractions', level=CommandLevels.MOD)
    def infraction_duration(self, event, infraction, duration):
        try:
            inf = Infraction.get(id=infraction)
        except Infraction.DoesNotExist:
            raise CommandFail('invalid infraction (try `!infractions recent`)')

        if inf.actor_id != event.author.id and event.user_level < CommandLevels.ADMIN:
            raise CommandFail('only administrators can modify the duration of infractions created by other moderators')

        if not inf.active:
            raise CommandFail('that infraction is not active and cannot be updated')

        expires_dt = parse_duration(duration, inf.created_at)

        converted = False
        if inf.type_ in [Infraction.Types.MUTE.index, Infraction.Types.BAN.index]:
            inf.type_ = Infraction.Types.TEMPMUTE if inf.type_ == Infraction.Types.MUTE.index else Infraction.Types.TEMPBAN
            converted = True
        elif inf.type_ not in [Infraction.Types.TEMPMUTE.index, Infraction.Types.TEMPBAN.index]:
            raise CommandFail('cannot set the duration for that type of infraction')

        self.inf_task.set_next_schedule(expires_dt)
        inf.expires_at = expires_dt
        inf.save()

        if converted:
            raise CommandSuccess('ok, I\'ve made that infraction temporary, it will now expire on {}'.format(
                inf.expires_at.isoformat()
            ))
        else:
            raise CommandSuccess('ok, I\'ve updated that infractions duration, it will now expire on {}'.format(
                inf.expires_at.isoformat()
            ))

    @Plugin.command('reason', '<infraction:int> <reason:str...>', level=CommandLevels.MOD)
    def reason(self, event, infraction, reason):
        try:
            inf = Infraction.get(id=infraction)
        except Infraction.DoesNotExist:
            inf = None

        if inf is None or inf.guild_id != event.guild.id:
            event.msg.reply('Unknown infraction ID')
            return

        if not inf.actor_id:
            inf.actor_id = event.author.id

        if inf.actor_id != event.author.id and event.user_level < CommandLevels.ADMIN:
            raise CommandFail('only administrators cannot modify other users infractions')

        inf.reason = reason
        inf.save()

        raise CommandSuccess('I\'ve updated the reason for infraction #{}'.format(inf.id))

    @Plugin.command('roles', level=CommandLevels.MOD)
    def roles(self, event):
        buff = ''
        for role in event.guild.roles.values():
            role = S(u'{} - {}\n'.format(role.id, role.name), escape_codeblocks=True)
            if len(role) + len(buff) > 1990:
                event.msg.reply(u'```{}```'.format(buff))
                buff = ''
            buff += role
        return event.msg.reply(u'```{}```'.format(buff))

    @Plugin.command('restore', '<user:user>', level=CommandLevels.MOD, group='backups')
    def restore(self, event, user):
        member = self.guild.get_member(user)
        if member:
            self.restore_user(event, member)
        else:
            raise CommandFail('invalid user')

    @Plugin.command('clear', '<user:snowflake>', level=CommandLevels.MOD, group='backups')
    def backups_clear(self, event, user_id):
        deleted = bool(GuildMemberBackup.delete().where(
            (GuildMemberBackup.user_id == user_id) &
            (GuildMemberBackup.guild_id == event.guild.id)
        ).execute())

        if deleted:
            event.msg.reply(':ok_hand: I\'ve cleared the member backup for that user')
        else:
            raise CommandFail('I couldn\t find any member backups for that user')

    def can_act_on(self, event, victim):
        actor_level = self.bot.plugins.get('CorePlugin').get_level(event.guild, event.author)
        victim_level = self.bot.plugins.get('CorePlugin').get_level(event.guild, victim)

        if actor_level <= victim_level:
            raise CommandFail('Invalid Permissions')

    @Plugin.command('mute', '<user:user|snowflake> [reason:str...]', level=CommandLevels.MOD)
    def mute(self, event, user, reason=None):
        member = event.guild.get_member(user)
        if member:
            self.can_act_on(event, member)
            if not event.config.mute_role:
                raise CommandFail('mute is not setup on this server')

            if len({event.config.temp_mute_role, event.config.mute_role} & set(member.roles)):
                raise CommandFail('{} is already muted'.format(member.user))

            Infraction.mute(self, event, member, reason)
            if event.config.confirm_actions:
                event.msg.reply(maybe_string(
                    reason,
                    u':ok_hand: {u} is now muted (`{o}`)',
                    u':ok_hand: {u} is now muted',
                    u=member.user,
                ))
        else:
            raise CommandFail('invalid user')

    @Plugin.command('tempmute', '<user:user|snowflake> <duration:str> [reason:str...]', level=CommandLevels.MOD)
    def tempmute(self, event, user, duration, reason=None):
        member = event.guild.get_member(user)
        if member:
            self.can_act_on(event, member)
            if not event.config.temp_mute_role and not event.config.mute_role:
                raise CommandFail('mute is not setup on this server')

            if len({event.config.temp_mute_role, event.config.mute_role} & set(member.roles)):
                raise CommandFail('{} is already muted'.format(member.user))

            expire_dt = parse_duration(duration)

            # Reset the infraction task so we make sure it runs after this new infraction
            self.inf_task.set_next_schedule(expire_dt)

            # Create the infraction
            Infraction.tempmute(self, event, member, reason, expire_dt)

            if event.config.confirm_actions:
                event.msg.reply(maybe_string(
                    reason,
                    u':ok_hand: {u} is now muted for {t} (`{o}`)',
                    u':ok_hand: {u} is now muted for {t}',
                    u=member.user,
                    t=humanize.naturaldelta(expire_dt - datetime.utcnow()),
                ))
        else:
            raise CommandFail('invalid user')

    @Plugin.command('unmute', '<user:user|snowflake>', level=CommandLevels.MOD)
    def unmute(self, event, user, reason=None):
        # TOOD: eventually we should pull the role from the GuildMemberBackup if they arent in server
        member = event.guild.get_member(user)

        if member:
            self.can_act_on(event, member)
            if not event.config.temp_mute_role and not event.config.mute_role:
                raise CommandFail('mute is not setup on this server')

            roles = {event.config.temp_mute_role, event.config.mute_role} & set(member.roles)
            if not len(roles):
                raise CommandFail('{} is not muted'.format(member.user))

            Infraction.update(
                active=False
            ).where(
                (Infraction.guild_id == event.guild.id) &
                (Infraction.user_id == member.user.id) &
                (Infraction.type_ == Infraction.Types.TEMPMUTE) &
                (Infraction.active == 1)
            ).execute()

            self.bot.plugins.get('ModLogPlugin').create_debounce(event, member.user.id, 'unmuted', actor=unicode(event.author), roles=roles)

            for role in roles:
                member.remove_role(role)

            if event.config.confirm_actions:
                event.msg.reply(u':ok_hand: {} is now unmuted'.format(member.user))
        else:
            raise CommandFail('invalid user')

    @Plugin.command('kick', '<user:user|snowflake> [reason:str...]', level=CommandLevels.MOD)
    def kick(self, event, user, reason=None):
        member = event.guild.get_member(user)
        if member:
            self.can_act_on(event, member)
            Infraction.kick(self, event, member, reason)
            if event.config.confirm_actions:
                event.msg.reply(maybe_string(
                    reason,
                    u':ok_hand: kicked {u} (`{o}`)',
                    u':ok_hand: kicked {u}',
                    u=member.user,
                ))
        else:
            raise CommandFail('invalid user')

    @Plugin.command('ban', '<user:user|snowflake> [reason:str...]', level=CommandLevels.MOD)
    @Plugin.command('forceban', '<user:snowflake> [reason:str...]', level=CommandLevels.MOD)
    def ban(self, event, user, reason=None):
        member = None

        if isinstance(user, (int, long)):
            self.can_act_on(event, user)
            Infraction.ban(self, event, user, reason, guild=event.guild)
        else:
            member = event.guild.get_member(user)
            if member:
                self.can_act_on(event, member)
                Infraction.ban(self, event, member, reason, guild=event.guild)
            else:
                raise CommandFail('invalid user')

        if event.config.confirm_actions:
            event.msg.reply(maybe_string(
                reason,
                u':ok_hand: banned {u} (`{o}`)',
                u':ok_hand: banned {u}',
                u=member.user if member else user,
            ))

    @Plugin.command('softban', '<user:user|snowflake> [reason:str...]', level=CommandLevels.MOD)
    def softban(self, event, user, reason=None):
        """
        Ban then unban a user from the server (with an optional reason for the modlog)
        """
        member = event.guild.get_member(user)
        if member:
            self.can_act_on(event, member)
            Infraction.softban(self, event, member, reason)
            if event.config.confirm_actions:
                event.msg.reply(maybe_string(
                    reason,
                    u':ok_hand: soft-banned {u} (`{o}`)',
                    u':ok_hand: soft-banned {u}',
                    u=member.user,
                ))
        else:
            raise CommandFail('invald user')

    @Plugin.command('tempban', '<user:user|snowflake> <duration:str> [reason:str...]', level=CommandLevels.MOD)
    def tempban(self, event, duration, user, reason=None):
        member = event.guild.get_member(user)
        if member:
            self.can_act_on(event, member)
            expires_dt = parse_duration(duration)
            self.inf_task.set_next_schedule(expires_dt)
            Infraction.tempban(self, event, member, reason, expires_dt)
            if event.config.confirm_actions:
                event.msg.reply(maybe_string(
                    reason,
                    u':ok_hand: temp-banned {u} for {t} (`{o}`)',
                    u':ok_hand: temp-banned {u} for {t}',
                    u=member.user,
                    t=humanize.naturaldelta(expires_dt - datetime.utcnow()),
                ))
        else:
            raise CommandFail('invalid user')

    @Plugin.command('archive here', '[size:int]', level=CommandLevels.MOD, context={'mode': 'all'})
    @Plugin.command('archive all', '[size:int]', level=CommandLevels.MOD, context={'mode': 'all'})
    @Plugin.command('archive user', '<user:user|snowflake> [size:int]', level=CommandLevels.MOD, context={'mode': 'user'})
    @Plugin.command('archive channel', '<channel:channel|snowflake> [size:int]', level=CommandLevels.MOD, context={'mode': 'channel'})
    def archive(self, event, size=50, mode=None, user=None, channel=None):
        if 0 > size >= 15000:
            raise CommandFail('too many messages must be between 1-15000')

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
        """
        Removes messages
        """
        if 0 > size >= 10000:
            raise CommandFail('too many messages must be between 1-10000')

        lock = rdb.lock('clean-{}'.format(event.channel.id))
        if not lock.acquire(blocking=False):
            raise CommandFail('already running a clean on this channel')

        try:
            query = Message.select().where(
                (Message.deleted >> False) &
                (Message.channel_id == event.channel.id) &
                (Message.timestamp > (datetime.utcnow() - timedelta(days=13)))
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

    @Plugin.command('add', '<user:user> <role:str> [reason:str...]', level=CommandLevels.MOD, context={'mode': 'add'}, group='role')
    @Plugin.command('rmv', '<user:user> <role:str> [reason:str...]', level=CommandLevels.MOD, context={'mode': 'remove'}, group='role')
    @Plugin.command('remove', '<user:user> <role:str> [reason:str...]', level=CommandLevels.MOD, context={'mode': 'remove'}, group='role')
    def role_add(self, event, user, role, reason=None, mode=None):
        role_obj = None

        if role.isdigit() and int(role) in event.guild.roles.keys():
            role_obj = event.guild.roles[int(role)]
        else:
            # First try exact match
            exact_matches = [i for i in event.guild.roles.values() if i.name.lower().replace(' ', '') == role.lower()]
            if len(exact_matches) == 1:
                role_obj = exact_matches[0]
            else:
                # Otherwise we fuzz it up
                rated = sorted([
                    (fuzz.partial_ratio(role, r.name.replace(' ', '')), r) for r in event.guild.roles.values()
                ], key=lambda i: i[0], reverse=True)

                if rated[0][0] > 40:
                    if len(rated) == 1:
                        role_obj = rated[0][1]
                    elif rated[0][0] - rated[1][0] > 20:
                        role_obj = rated[0][1]

        if not role_obj:
            raise CommandFail('too many matches for that role, try something more exact or the role ID')

        author_member = event.guild.get_member(event.author)
        highest_role = sorted([event.guild.roles.get(r) for r in author_member.roles], key=lambda i: i.position, reverse=True)
        if not author_member.owner and (not highest_role or highest_role[0].position < role_obj.position):
            raise CommandFail('you can only {} roles that are ranked lower than your highest role'.format(mode))

        member = event.guild.get_member(user)
        if not member:
            raise CommandFail('invalid member')

        self.can_act_on(event, member)

        if mode == 'add' and role_obj.id in member.roles:
            raise CommandFail(u'{} already has the {} role'.format(member, role_obj.name))
        elif mode == 'remove' and role_obj.id not in member.roles:
            return CommandFail(u'{} doesn\'t have the {} role'.format(member, role_obj.name))

        self.bot.plugins.get('ModLogPlugin').create_debounce(
            event, member.user.id, mode + '_role', actor=event.author, reason=reason or 'no reason')

        if mode == 'add':
            member.add_role(role_obj.id)
        else:
            member.remove_role(role_obj.id)

        event.msg.reply(u':ok_hand: {} role {} to {}'.format('added' if mode == 'add' else 'removed',
            role_obj.name,
            member))

    @Plugin.command('stats', '<user:user>', level=CommandLevels.MOD)
    def msgstats(self, event, user):
        # Query for the basic aggregate message statistics
        q = list(Message.select(
            fn.Count('*'),
            fn.Sum(fn.char_length(Message.content)),
            fn.Sum(fn.array_length(Message.emojis, 1)),
            fn.Sum(fn.array_length(Message.mentions, 1)),
            fn.Sum(fn.array_length(Message.attachments, 1)),
        ).where(
            (Message.author_id == user.id)
        ).tuples())[0]

        reactions_given = list(Reaction.select(
            fn.Count('*'),
            Reaction.emoji_id,
            Reaction.emoji_name,
        ).join(
            Message,
            on=(Message.id == Reaction.message_id)
        ).where(
            (Reaction.user_id == user.id)
        ).group_by(Reaction.emoji_id, Reaction.emoji_name).order_by(fn.Count('*').desc()).tuples())

        # Query for most used emoji
        emojis = list(Message.raw('''
            SELECT gm.emoji_id, gm.name, count(*)
            FROM (
                SELECT unnest(emojis) as id
                FROM messages
                WHERE author_id=%s
            ) q
            JOIN guildemojis gm ON gm.emoji_id=q.id
            GROUP BY 1, 2
            ORDER BY 3 DESC
            LIMIT 1
        ''', (user.id, )).tuples())

        deleted = Message.select().where(
            (Message.author_id == user.id) &
            (Message.deleted == 1)
        ).count()

        embed = MessageEmbed()
        embed.fields.append(
            MessageEmbedField(name='Total Messages Sent', value=q[0] or '0', inline=True))
        embed.fields.append(
            MessageEmbedField(name='Total Characters Sent', value=q[1] or '0', inline=True))
        embed.fields.append(
            MessageEmbedField(name='Total Deleted Messages', value=deleted or '0', inline=True))
        embed.fields.append(
            MessageEmbedField(name='Total Custom Emoji\'s', value=q[2] or '0', inline=True))
        embed.fields.append(
            MessageEmbedField(name='Total Mentions', value=q[3] or '0', inline=True))
        embed.fields.append(
            MessageEmbedField(name='Total Attachments', value=q[4] or '0', inline=True))
        embed.fields.append(
            MessageEmbedField(name='Total Reactions', value=sum(i[0] for i in reactions_given), inline=True))

        if reactions_given:
            emoji = reactions_given[0][2] if not reactions_given[0][1] else '<:{}:{}>'.format(reactions_given[0][2], reactions_given[0][1])
            embed.fields.append(
                MessageEmbedField(name='Most Used Reaction', value=u'{} (used {} times)'.format(
                    emoji,
                    reactions_given[0][0],
                ), inline=True))

        if emojis:
            embed.fields.append(
                MessageEmbedField(name='Most Used Emoji', value=u'<:{1}:{0}> (`{1}`, used {2} times)'.format(*emojis[0])))

        embed.thumbnail = MessageEmbedThumbnail(url=user.avatar_url)
        embed.color = get_dominant_colors_user(user)
        event.msg.reply('', embed=embed)

    @Plugin.command('emojistats', '<mode:str> <sort:str>', level=CommandLevels.MOD)
    def emojistats_custom(self, event, mode, sort):
        if mode not in ('server', 'global'):
            raise CommandFail('invalid emoji mode, must be `server` or `global`')

        if sort not in ('least', 'most'):
            raise CommandFail('invalid emoji sort, must be `least` or `most`')

        order = 'DESC' if sort == 'most' else 'ASC'

        if mode == 'server':
            q = CUSTOM_EMOJI_STATS_SERVER_SQL.format(order, guild=event.guild.id)
        else:
            q = CUSTOM_EMOJI_STATS_GLOBAL_SQL.format(order, guild=event.guild.id)

        q = list(GuildEmoji.raw(q).tuples())

        tbl = MessageTable()
        tbl.set_header('Count', 'Name', 'ID')
        for emoji_id, name, count in q:
            tbl.add(count, name, emoji_id)

        event.msg.reply(tbl.compile())

    @Plugin.command('prune', '[uses:int]', level=CommandLevels.ADMIN, group='invites')
    def invites_prune(self, event, uses=1):
        invites = [
            i for i in event.guild.get_invites()
            if i.uses <= uses and i.created_at < (datetime.utcnow() - timedelta(hours=1))
        ]

        if not invites:
            return event.msg.reply('I didn\'t find any invites matching your criteria')

        msg = event.msg.reply('Ok, a total of {} invites created by {} users with {} total uses would be pruned.'.format(
            len(invites),
            len({i.inviter.id for i in invites}),
            sum(i.uses for i in invites)
        ))

        msg.chain(False).\
            add_reaction(GREEN_TICK_EMOJI).\
            add_reaction(RED_TICK_EMOJI)

        try:
            event = self.wait_for_event(
                'MessageReactionAdd',
                message_id=msg.id,
                conditional=lambda e: (
                    e.emoji.id in (GREEN_TICK_EMOJI_ID, RED_TICK_EMOJI_ID) and
                    e.user_id != self.state.me.id
                )).get(timeout=10)
        except gevent.Timeout:
            msg.reply('Not executing invite prune')
            msg.delete()
            return

        msg.delete()

        if event.emoji.id == GREEN_TICK_EMOJI_ID:
            msg = msg.reply('Pruning invites...')
            for invite in invites:
                invite.delete()
            msg.edit('Ok, invite prune completed')
        else:
            msg = msg.reply('Not pruning invites')

    @Plugin.command('clean', '<user:user|snowflake> [count:int] [emoji:str]', level=CommandLevels.MOD, group='reactions')
    def reactions_clean(self, event, user, count=10, emoji=None):
        if isinstance(user, DiscoUser):
            user = user.id

        if count > 50:
            raise CommandFail('cannot clean more than 50 reactions')

        lock = rdb.lock('clean-reactions-{}'.format(user))
        if not lock.acquire(blocking=False):
            raise CommandFail('already running a clean on user')

        query = [
            (Reaction.user_id == user),
            (Message.guild_id == event.guild.id),
            (Message.deleted == 0),
        ]

        if emoji:
            emoji_id = EMOJI_RE.findall(emoji)
            if emoji_id:
                query.append((Reaction.emoji_id == emoji_id[0]))
            else:
                # TODO: validation?
                query.append((Reaction.emoji_name == emoji))

        try:
            reactions = list(Reaction.select(
                Reaction.message_id,
                Reaction.emoji_id,
                Reaction.emoji_name,
                Message.channel_id,
            ).join(
                Message,
                on=(Message.id == Reaction.message_id),
            ).where(
                reduce(operator.and_, query)
            ).order_by(Reaction.message_id.desc()).limit(count).tuples())

            if not reactions:
                raise CommandFail('no reactions to purge')

            msg = event.msg.reply('Hold on while I clean {} reactions'.format(
                len(reactions)
            ))

            for message_id, emoji_id, emoji_name, channel_id in reactions:
                if emoji_id:
                    emoji = '{}:{}'.format(emoji_name, emoji_id)
                else:
                    emoji = emoji_name

                self.client.api.channels_messages_reactions_delete(
                    channel_id,
                    message_id,
                    emoji,
                    user)

            msg.edit('Ok, I cleaned {} reactions'.format(
                len(reactions),
            ))
        finally:
            lock.release()

    @Plugin.command('log', '<user:user|snowflake>', group='voice', level=CommandLevels.MOD)
    def voice_log(self, event, user):
        if isinstance(user, DiscoUser):
            user = user.id

        sessions = GuildVoiceSession.select(
            GuildVoiceSession.user_id,
            GuildVoiceSession.channel_id,
            GuildVoiceSession.started_at,
            GuildVoiceSession.ended_at
        ).where(
            (GuildVoiceSession.user_id == user) &
            (GuildVoiceSession.guild_id == event.guild.id)
        ).order_by(GuildVoiceSession.started_at.desc()).limit(10)

        tbl = MessageTable()
        tbl.set_header('Channel', 'Joined At', 'Duration')

        for session in sessions:
            tbl.add(
                unicode(self.state.channels.get(session.channel_id) or 'UNKNOWN'),
                '{} ({} ago)'.format(
                    session.started_at.isoformat(),
                    humanize.naturaldelta(datetime.utcnow() - session.started_at)),
                humanize.naturaldelta(session.ended_at - session.started_at) if session.ended_at else 'Active')

        event.msg.reply(tbl.compile())
