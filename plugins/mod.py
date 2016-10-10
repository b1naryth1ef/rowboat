import re
import time

from holster.enum import Enum

from disco.bot import Plugin, Config
from disco.types.permissions import Permissions


ActionType = Enum(
    'NONE',
    'DELETE_MSG',
    'CHANGE_NICK',
    'KICK_USER',
    'BAN_USER'
)


class Count(object):
    __slots__ = ['count', 'reset_at']

    def __init__(self, count, reset_at):
        self.count = count
        self.reset_at = reset_at


class ModPluginConfig(Config):
    whitelist_roles = []

    filter_invites = False
    filter_invites_re = r'(discord.me|discord.gg)(?:/#)?(?:/invite)?/([a-z0-9\-]+)'
    filter_invites_whitelist = []

    filter_nicknames = False
    filter_nicknames_words = {}
    filter_nicknames_prefixes = []

    filter_usernames = False
    filter_usernames_startup = False
    filter_usernames_words = {}
    filter_usernames_prefixes = []

    mention_tracker = False
    mention_threshold = 20
    mention_reset_time = 60
    mention_action = 'kick_user'

    spam_detection = False
    spam_threshold = 15
    spam_reset_time = 30
    spam_action = 'none'


@Plugin.with_config(ModPluginConfig)
class ModPlugin(Plugin):
    def __init__(self, bot, config, *args, **kwargs):
        super(ModPlugin, self).__init__(bot, config or ModPluginConfig(), *args, **kwargs)

        self.mention_tracking = {}
        self.spam_tracking = {}
        self.invite_re = re.compile(self.config.filter_invites_re, re.I)

    def action(self, action, entity, reason, *metadata):
        if isinstance(action, str):
            action = ActionType.get(action)

        context = ""

        if action is ActionType.DELETE_MSG and entity.guild.can(self.state.me, Permissions.MANAGE_MESSAGES):
            entity.delete()
            context = u'author = `{}`\n channel = `{} ({})` \n message = ```\n{}\n```'.format(
                entity.author, entity.channel.name, entity.channel.id, entity.content)
        elif action is ActionType.CHANGE_NICK and entity.guild.can(self.state.me, Permissions.MANAGE_NICKNAMES):
            entity.set_nickname(metadata[0])
            context = u'user = `{}`\n before = `{}`\n after = `{}`'.format(entity.user, entity.nick, metadata[0])
        elif action is ActionType.KICK_USER and entity.guild.can(self.state.me, Permissions.KICK_MEMBERS):
            entity.kick()
            context = u'user = `{}`'.format(entity.user)
        elif action is ActionType.BAN_USER and entity.guild.can(self.state.me, Permissions.BAN_MEMBERS):
            entity.ban()
            context = u'user = `{}`'.format(entity.user)
        else:
            context = u'user = `{}`'.format(entity.user)

        self.log.info('[{}] {} for {}'.format(action.name.upper(), entity, reason))

        channel = next((i for i in entity.guild.channels.values() if i.name == 'mod-log'), None)
        if not channel:
            return

        channel.send_message('[**{}**] {}\n {}'.format(action.name.upper(), reason, context))

    @Plugin.schedule(500)
    def cleanup(self):
        for user, obj in self.mention_tracking.items():
            if obj.reset_at < time.time():
                del self.mention_tracking[user]

    @Plugin.command('block', '<entity:mention>')
    def block(self, event, entity):
        """
        Blocks the given user/role from the current channel.
        Blocks the given user from the current channel.
        """

        event.channel.create_overwrite(entity, deny=Permissions.READ_MESSAGES)
        event.msg.reply('Blocked {} from viewing this channel'.format(entity))

    @Plugin.listen('GuildCreate')
    def on_guild_create(self, event):
        if self.config.filter_usernames_startup:
            self.filter_usernames_guild(event.guild)

    @Plugin.listen('GuildMemberAdd')
    def on_guild_member_add(self, event):
        if self.config.filter_usernames:
            self.step_filter_username(event)

    @Plugin.listen('GuildMemberUpdate')
    def on_guild_member_update(self, event):
        if self.config.filter_nicknames:
            self.step_filter_nickname(event)

        if self.config.filter_usernames:
            self.step_filter_username(event)

    @Plugin.listen('MessageCreate')
    def on_message_create(self, event):
        if not event.message.channel:
            return

        if not event.message.channel.guild or event.message.author.id == self.state.me.id:
            return

        member = event.message.channel.guild.members.get(event.message.author.id)
        if not member or set(self.config.whitelist_roles) & set(member.roles):
            return

        if self.config.filter_invites:
            self.step_filter_invites(event.message)

        if self.config.mention_tracker:
            self.step_track_mentions(event.message)

        if self.config.spam_detection:
            self.step_spam_detection(event.message)

    def step_track_mentions(self, message):
        total_mentioned = len(message.mentions) + len(message.mention_roles)
        if not total_mentioned:
            return

        if message.author.id in self.mention_tracking:
            obj = self.mention_tracking[message.author.id]
            if time.time() > obj.reset_at:
                obj.reset_at = time.time() + self.config.mention_reset_time
                obj.count = total_mentioned
                return
            else:
                obj.count += total_mentioned
                if obj.count > self.config.mention_threshold:
                    self.action(
                        self.config.mention_action,
                        message.member,
                        'mention spam')
                    del self.mention_tracking[message.author.id]
        else:
            self.mention_tracking[message.author.id] = Count(
                    total_mentioned, time.time() + self.config.mention_reset_time)

    def step_filter_invites(self, message):
        if not message.content:
            return

        matches = [i[1] for i in self.invite_re.findall(message.content) if i[1] not in self.config.filter_invites_whitelist]

        if not len(matches):
            return

        self.action(ActionType.DELETE_MSG, message, 'contained invites `{}`'.format(' '.join(matches)))

    def step_spam_detection(self, message):
        if message.author.id in self.spam_tracking:
            obj = self.spam_tracking[message.author.id]

            if time.time() > obj.reset_at:
                obj.reset_at = time.time() + self.config.spam_reset_time
                obj.count = 1
                return
            else:
                obj.count += 1
                if obj.count > self.config.spam_threshold:
                    self.action(
                        self.config.spam_action,
                        message.member,
                        'message spam')
                    # TODO: delete messages
                    del self.spam_tracking[message.author.id]
        else:
            self.spam_tracking[message.author.id] = Count(1, time.time() + self.config.spam_reset_time)

    def step_filter_nickname(self, event):
        if event.member.nick:
            changed, new = self.check_nickname(event.member)

            if changed:
                self.action(
                    ActionType.CHANGE_NICK,
                    event.member,
                    'matched nickname filter',
                    new)

    def step_filter_username(self, event):
        if event.member.nick:
            return

        changed, new = self.check_username(event.member)

        if changed and not event.member.nick == new:
            self.action(
                ActionType.CHANGE_NICK,
                event.member,
                'matched username filter',
                new)

    def check_nickname(self, member):
        raw = member.nick

        for word, repl in self.config.filter_nicknames_words.items():
            raw = re.compile(word, re.IGNORECASE).sub(repl, raw)

        for pref in self.config.filter_nicknames_prefixes:
            if raw.startswith(pref):
                raw = raw[len(pref):]

        if raw != member.nick:
            return True, raw
        return False, None

    def check_username(self, member):
        raw = member.user.username

        for word, repl in self.config.filter_usernames_words.items():
            raw = re.compile(word, re.IGNORECASE).sub(repl, raw)

        for pref in self.config.filter_usernames_prefixes:
            if raw.startswith(pref):
                raw = raw[len(pref):]

        if raw != member.user.username:
            return True, raw

        return False, None

    def filter_usernames_guild(self, guild):
        for member in guild.members.values():
            if member.nick:
                continue

            changed, username = self.check_username(member)

            if changed and member.nick != username:
                member.set_nickname(username)
