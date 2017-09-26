import re
import time
import gevent
import humanize
import operator

from peewee import fn
from holster.emitter import Priority
from fuzzywuzzy import fuzz

from datetime import datetime, timedelta

from disco.bot import CommandLevels
from disco.types.user import User as DiscoUser
from disco.types.message import MessageTable, MessageEmbed, MessageEmbedField, MessageEmbedThumbnail
from disco.types.permissions import Permissions
from disco.util.functional import chunks
from disco.util.sanitize import S

from rowboat.plugins import RowboatPlugin as Plugin, CommandFail, CommandSuccess
from rowboat.util.images import get_dominant_colors_user
from rowboat.util.input import parse_duration
from rowboat.util.gevent import wait_many
from rowboat.redis import rdb
from rowboat.types import Field, DictField, ListField, snowflake, SlottedModel
from rowboat.types.plugin import PluginConfig
from rowboat.plugins.modlog import Actions
from rowboat.models.user import User
from rowboat.models.guild import GuildMemberBackup, GuildEmoji, GuildVoiceSession
from rowboat.models.message import Message, Reaction, MessageArchive
from rowboat.constants import (
    GREEN_TICK_EMOJI_ID, RED_TICK_EMOJI_ID, GREEN_TICK_EMOJI, RED_TICK_EMOJI
)

EMOJI_RE = re.compile(r'<:[a-zA-Z0-9_]+:([0-9]+)>')

CUSTOM_EMOJI_STATS_SERVER_SQL = """
SELECT gm.emoji_id, gm.name, count(*) FROM guild_emojis gm
JOIN messages m ON m.emojis @> ARRAY[gm.emoji_id]
WHERE gm.deleted=false AND gm.guild_id={guild} AND m.guild_id={guild}
GROUP BY 1, 2
ORDER BY 3 {}
LIMIT 30
"""

CUSTOM_EMOJI_STATS_GLOBAL_SQL = """
SELECT gm.emoji_id, gm.name, count(*) FROM guild_emojis gm
JOIN messages m ON m.emojis @> ARRAY[gm.emoji_id]
WHERE gm.deleted=false AND gm.guild_id={guild}
GROUP BY 1, 2
ORDER BY 3 {}
LIMIT 30
"""


class PersistConfig(SlottedModel):
    roles = Field(bool, default=False)
    nickname = Field(bool, default=False)
    voice = Field(bool, default=False)

    role_ids = ListField(snowflake, default=[])


class AdminConfig(PluginConfig):
    confirm_actions = Field(bool, default=True)

    # Role saving information
    persist = Field(PersistConfig, default=None)

    # Aliases to roles, can be used in place of IDs in commands
    role_aliases = DictField(unicode, snowflake)

    # Group roles can be joined/left by any user
    group_roles = DictField(lambda value: unicode(value).lower(), snowflake)
    group_confirm_reactions = Field(bool, default=False)

    # Locked roles cannot be changed unless they are unlocked w/ command
    locked_roles = ListField(snowflake)


@Plugin.with_config(AdminConfig)
class AdminPlugin(Plugin):
    def load(self, ctx):
        super(AdminPlugin, self).load(ctx)

        self.cleans = {}
        self.unlocked_roles = {}
        self.role_debounces = {}

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

        self.call(
            'ModLogPlugin.create_debounce',
            event,
            ['GuildMemberUpdate'],
        )

        member.modify(**kwargs)

        self.call(
            'ModLogPlugin.log_action_ext',
            Actions.MEMBER_RESTORE,
            event.guild.id,
            member=member,
        )

    @Plugin.listen('GuildMemberRemove', priority=Priority.BEFORE)
    def on_guild_member_remove(self, event):
        if event.user.id in event.guild.members:
            GuildMemberBackup.create_from_member(event.guild.members.get(event.user.id))

    @Plugin.listen('GuildMemberAdd')
    def on_guild_member_add(self, event):
        if not event.config.persist:
            return

        self.restore_user(event, event.member)

    @Plugin.listen('GuildRoleUpdate', priority=Priority.BEFORE)
    def on_guild_role_update(self, event):
        if event.role.id not in event.config.locked_roles:
            return

        if event.role.id in self.unlocked_roles and self.unlocked_roles[event.role.id] > time.time():
            return

        if event.role.id in self.role_debounces:
            if self.role_debounces.pop(event.role.id) > time.time():
                return

        role_before = event.guild.roles.get(event.role.id)
        if not role_before:
            return

        to_update = {}
        for field in ('name', 'hoist', 'color', 'permissions', 'position'):
            if getattr(role_before, field) != getattr(event.role, field):
                to_update[field] = getattr(role_before, field)

        if to_update:
            self.log.warning('Rolling back update to roll %s (in %s), roll is locked', event.role.id, event.guild_id)
            self.role_debounces[event.role.id] = time.time() + 60
            event.role.update(**to_update)

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
        member = event.guild.get_member(user)
        if member:
            self.restore_user(event, member)
        else:
            raise CommandFail('invalid user')

    @Plugin.command('clear', '<user_id:snowflake>', level=CommandLevels.MOD, group='backups')
    def backups_clear(self, event, user_id):
        deleted = bool(GuildMemberBackup.delete().where(
            (GuildMemberBackup.user_id == user_id) &
            (GuildMemberBackup.guild_id == event.guild.id)
        ).execute())

        if deleted:
            event.msg.reply(':ok_hand: I\'ve cleared the member backup for that user')
        else:
            raise CommandFail('I couldn\t find any member backups for that user')

    def can_act_on(self, event, victim_id, throw=True):
        if event.author.id == victim_id:
            if not throw:
                return False
            raise CommandFail('cannot execute that action on yourself')

        victim_level = self.bot.plugins.get('CorePlugin').get_level(event.guild, victim_id)

        if event.user_level <= victim_level:
            if not throw:
                return False
            raise CommandFail('invalid permissions')

        return True

    @Plugin.command('here', '[size:int]', level=CommandLevels.MOD, context={'mode': 'all'}, group='archive')
    @Plugin.command('all', '[size:int]', level=CommandLevels.MOD, context={'mode': 'all'}, group='archive')
    @Plugin.command(
        'user',
        '<user:user|snowflake> [size:int]',
        level=CommandLevels.MOD,
        context={'mode': 'user'},
        group='archive')
    @Plugin.command(
        'channel',
        '<channel:channel|snowflake> [size:int]',
        level=CommandLevels.MOD,
        context={'mode': 'channel'},
        group='archive')
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

    @Plugin.command('extend', '<archive_id:str> <duration:str>', level=CommandLevels.MOD, group='archive')
    def archive_extend(self, event, archive_id, duration):
        try:
            archive = MessageArchive.get(archive_id=archive_id)
        except MessageArchive.DoesNotExist:
            raise CommandFail('invalid message archive id')

        archive.expires_at = parse_duration(duration)

        MessageArchive.update(
            expires_at=parse_duration(duration)
        ).where(
            (MessageArchive.archive_id == archive_id)
        ).execute()

        raise CommandSuccess('duration of archive {} has been extended (<{}>)'.format(
            archive_id,
            archive.url,
        ))

    @Plugin.command('clean cancel', level=CommandLevels.MOD)
    def clean_cacnel(self, event):
        if event.channel.id not in self.cleans:
            raise CommandFail('no clean is running in this channel')

        self.cleans[event.channel.id].kill()
        event.msg.reply('Ok, the running clean was cancelled')

    @Plugin.command('clean all', '[size:int]', level=CommandLevels.MOD, context={'mode': 'all'})
    @Plugin.command('clean bots', '[size:int]', level=CommandLevels.MOD, context={'mode': 'bots'})
    @Plugin.command('clean user', '<user:user> [size:int]', level=CommandLevels.MOD, context={'mode': 'user'})
    def clean(self, event, user=None, size=25, typ=None, mode='all'):
        """
        Removes messages
        """
        if 0 > size >= 10000:
            raise CommandFail('too many messages must be between 1-10000')

        if event.channel.id in self.cleans:
            raise CommandFail('a clean is already running on this channel')

        query = Message.select(Message.id).where(
            (Message.deleted >> False) &
            (Message.channel_id == event.channel.id) &
            (Message.timestamp > (datetime.utcnow() - timedelta(days=13)))
        ).join(User).order_by(Message.timestamp.desc()).limit(size)

        if mode == 'bots':
            query = query.where((User.bot >> True))
        elif mode == 'user':
            query = query.where((User.user_id == user.id))

        messages = [i[0] for i in query.tuples()]

        if len(messages) > 100:
            msg = event.msg.reply('Woah there, that will delete a total of {} messages, please confirm.'.format(
                len(messages)
            ))

            msg.chain(False).\
                add_reaction(GREEN_TICK_EMOJI).\
                add_reaction(RED_TICK_EMOJI)

            try:
                mra_event = self.wait_for_event(
                    'MessageReactionAdd',
                    message_id=msg.id,
                    conditional=lambda e: (
                        e.emoji.id in (GREEN_TICK_EMOJI_ID, RED_TICK_EMOJI_ID) and
                        e.user_id == event.author.id
                    )).get(timeout=10)
            except gevent.Timeout:
                return
            finally:
                msg.delete()

            if mra_event.emoji.id != GREEN_TICK_EMOJI_ID:
                return

            event.msg.reply(':wastebasket: Ok please hold on while I delete those messages...').after(5).delete()

        def run_clean():
            for chunk in chunks(messages, 100):
                self.client.api.channels_messages_delete_bulk(event.channel.id, chunk)

        self.cleans[event.channel.id] = gevent.spawn(run_clean)
        self.cleans[event.channel.id].join()
        del self.cleans[event.channel.id]

    @Plugin.command(
        'add',
        '<user:user> <role:str> [reason:str...]',
        level=CommandLevels.MOD,
        context={'mode': 'add'},
        group='role')
    @Plugin.command(
        'rmv',
        '<user:user> <role:str> [reason:str...]',
        level=CommandLevels.MOD,
        context={'mode': 'remove'},
        group='role')
    @Plugin.command('remove',
        '<user:user> <role:str> [reason:str...]',
        level=CommandLevels.MOD,
        context={'mode': 'remove'},
        group='role')
    def role_add(self, event, user, role, reason=None, mode=None):
        role_obj = None

        if role.isdigit() and int(role) in event.guild.roles.keys():
            role_obj = event.guild.roles[int(role)]
        elif role.lower() in event.config.role_aliases:
            role_obj = event.guild.roles.get(event.config.role_aliases[role.lower()])
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
        highest_role = sorted(
            [event.guild.roles.get(r) for r in author_member.roles],
            key=lambda i: i.position,
            reverse=True)
        if not author_member.owner and (not highest_role or highest_role[0].position <= role_obj.position):
            raise CommandFail('you can only {} roles that are ranked lower than your highest role'.format(mode))

        member = event.guild.get_member(user)
        if not member:
            raise CommandFail('invalid member')

        self.can_act_on(event, member.id)

        if mode == 'add' and role_obj.id in member.roles:
            raise CommandFail(u'{} already has the {} role'.format(member, role_obj.name))
        elif mode == 'remove' and role_obj.id not in member.roles:
            return CommandFail(u'{} doesn\'t have the {} role'.format(member, role_obj.name))

        self.call(
            'ModLogPlugin.create_debounce',
            event,
            ['GuildMemberUpdate'],
            role_id=role_obj.id,
        )

        if mode == 'add':
            member.add_role(role_obj.id)
        else:
            member.remove_role(role_obj.id)

        self.call(
            'ModLogPlugin.log_action_ext',
            (Actions.MEMBER_ROLE_ADD if mode == 'add' else Actions.MEMBER_ROLE_REMOVE),
            event.guild.id,
            member=member,
            role=role_obj,
            actor=unicode(event.author),
            reason=reason or 'no reason',
        )

        event.msg.reply(u':ok_hand: {} role {} to {}'.format('added' if mode == 'add' else 'removed',
            role_obj.name,
            member))

    @Plugin.command('stats', '<user:user>', level=CommandLevels.MOD)
    def msgstats(self, event, user):
        # Query for the basic aggregate message statistics
        message_stats = Message.select(
            fn.Count('*'),
            fn.Sum(fn.char_length(Message.content)),
            fn.Sum(fn.array_length(Message.emojis, 1)),
            fn.Sum(fn.array_length(Message.mentions, 1)),
            fn.Sum(fn.array_length(Message.attachments, 1)),
        ).where(
            (Message.author_id == user.id)
        ).tuples().async()

        reactions_given = Reaction.select(
            fn.Count('*'),
            Reaction.emoji_id,
            Reaction.emoji_name,
        ).join(
            Message,
            on=(Message.id == Reaction.message_id)
        ).where(
            (Reaction.user_id == user.id)
        ).group_by(
            Reaction.emoji_id, Reaction.emoji_name
        ).order_by(fn.Count('*').desc()).tuples().async()

        # Query for most used emoji
        emojis = Message.raw('''
            SELECT gm.emoji_id, gm.name, count(*)
            FROM (
                SELECT unnest(emojis) as id
                FROM messages
                WHERE author_id=%s
            ) q
            JOIN guild_emojis gm ON gm.emoji_id=q.id
            GROUP BY 1, 2
            ORDER BY 3 DESC
            LIMIT 1
        ''', (user.id, )).tuples().async()

        deleted = Message.select(
            fn.Count('*')
        ).where(
            (Message.author_id == user.id) &
            (Message.deleted == 1)
        ).tuples().async()

        wait_many(message_stats, reactions_given, emojis, deleted, timeout=10)

        # If we hit an exception executing the core query, throw an exception
        if message_stats.exception:
            message_stats.get()

        q = message_stats.value[0]
        embed = MessageEmbed()
        embed.fields.append(
            MessageEmbedField(name='Total Messages Sent', value=q[0] or '0', inline=True))
        embed.fields.append(
            MessageEmbedField(name='Total Characters Sent', value=q[1] or '0', inline=True))

        if deleted.value:
            embed.fields.append(
                MessageEmbedField(name='Total Deleted Messages', value=deleted.value[0][0], inline=True))
        embed.fields.append(
            MessageEmbedField(name='Total Custom Emojis', value=q[2] or '0', inline=True))
        embed.fields.append(
            MessageEmbedField(name='Total Mentions', value=q[3] or '0', inline=True))
        embed.fields.append(
            MessageEmbedField(name='Total Attachments', value=q[4] or '0', inline=True))

        if reactions_given.value:
            reactions_given = reactions_given.value

            embed.fields.append(
                MessageEmbedField(name='Total Reactions', value=sum(i[0] for i in reactions_given), inline=True))

            emoji = (
                reactions_given[0][2]
                if not reactions_given[0][1] else
                '<:{}:{}>'.format(reactions_given[0][2], reactions_given[0][1])
            )
            embed.fields.append(
                MessageEmbedField(name='Most Used Reaction', value=u'{} (used {} times)'.format(
                    emoji,
                    reactions_given[0][0],
                ), inline=True))

        if emojis.value:
            emojis = list(emojis.value)

            if emojis:
                embed.add_field(
                    name='Most Used Emoji',
                    value=u'<:{1}:{0}> (`{1}`, used {2} times)'.format(*emojis[0]))

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

        msg = event.msg.reply(
            'Ok, a total of {} invites created by {} users with {} total uses would be pruned.'.format(
                len(invites),
                len({i.inviter.id for i in invites}),
                sum(i.uses for i in invites)
            ))

        msg.chain(False).\
            add_reaction(GREEN_TICK_EMOJI).\
            add_reaction(RED_TICK_EMOJI)

        try:
            mra_event = self.wait_for_event(
                'MessageReactionAdd',
                message_id=msg.id,
                conditional=lambda e: (
                    e.emoji.id in (GREEN_TICK_EMOJI_ID, RED_TICK_EMOJI_ID) and
                    e.user_id == event.author.id
                )).get(timeout=10)
        except gevent.Timeout:
            msg.reply('Not executing invite prune')
            msg.delete()
            return

        msg.delete()

        if mra_event.emoji.id == GREEN_TICK_EMOJI_ID:
            msg = msg.reply('Pruning invites...')
            for invite in invites:
                invite.delete()
            msg.edit('Ok, invite prune completed')
        else:
            msg = msg.reply('Not pruning invites')

    @Plugin.command(
        'clean',
        '<user:user|snowflake> [count:int] [emoji:str]',
        level=CommandLevels.MOD,
        group='reactions')
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

    @Plugin.command('join', '<name:str>', aliases=['add', 'give'])
    def join_role(self, event, name):
        if not event.config.group_roles:
            return

        role = event.guild.roles.get(event.config.group_roles.get(name.lower()))
        if not role:
            raise CommandFail('invalid or unknown group')

        has_any_admin_perms = any(role.permissions.can(i) for i in (
            Permissions.KICK_MEMBERS,
            Permissions.BAN_MEMBERS,
            Permissions.ADMINISTRATOR,
            Permissions.MANAGE_CHANNELS,
            Permissions.MANAGE_GUILD,
            Permissions.MANAGE_MESSAGES,
            Permissions.MENTION_EVERYONE,
            Permissions.MUTE_MEMBERS,
            Permissions.MOVE_MEMBERS,
            Permissions.MANAGE_NICKNAMES,
            Permissions.MANAGE_ROLES,
            Permissions.MANAGE_WEBHOOKS,
            Permissions.MANAGE_EMOJIS,
        ))

        # Sanity check
        if has_any_admin_perms:
            raise CommandFail('cannot join group with admin permissions')

        member = event.guild.get_member(event.author)
        if role.id in member.roles:
            raise CommandFail('you are already a member of that group')

        member.add_role(role)
        if event.config.group_confirm_reactions:
            event.msg.add_reaction(GREEN_TICK_EMOJI)
            return
        raise CommandSuccess(u'you have joined the {} group'.format(name))

    @Plugin.command('leave', '<name:snowflake|str>', aliases=['remove', 'take'])
    def leave_role(self, event, name):
        if not event.config.group_roles:
            return

        role_id = event.config.group_roles.get(name.lower())
        if not role_id or role_id not in event.guild.roles:
            raise CommandFail('invalid or unknown group')

        member = event.guild.get_member(event.author)
        if role_id not in member.roles:
            raise CommandFail('you are not a member of that group')

        member.remove_role(role_id)
        if event.config.group_confirm_reactions:
            event.msg.add_reaction(GREEN_TICK_EMOJI)
            return
        raise CommandSuccess(u'you have left the {} group'.format(name))

    @Plugin.command('unlock', '<role_id:snowflake>', group='role', level=CommandLevels.ADMIN)
    def unlock_role(self, event, role_id):
        if role_id not in event.config.locked_roles:
            raise CommandFail('role %s is not locked' % role_id)

        if role_id in self.unlocked_roles and self.unlocked_roles[role_id] > time.time():
            raise CommandFail('role %s is already unlocked' % role_id)

        self.unlocked_roles[role_id] = time.time() + 300
        raise CommandSuccess('role is unlocked for 5 minutes')
