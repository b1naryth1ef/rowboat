import re
import time

from holster.enum import Enum

from disco.bot.plugin import Plugin
from disco.types.permissions import Permissions


ActionType = Enum(
    'DELETE_MSG',
    'CHANGE_NICK',
    'KICK_USER',
    'BAN_USER'
)


class UserMentions(object):
    __slots__ = ['count', 'reset_at']

    def __init__(self, count, reset_at):
        self.count = count
        self.reset_at = reset_at


class ModPluginConfig(object):
    whitelist_roles = []

    filter_invites = False
    filter_invites_re = r'(discord.me|discord.gg)(?:/#)?(?:/invite)?/([a-z0-9\-]+)'
    filter_invites_whitelist = []

    filter_nicknames = False
    filter_nickname_words = {}
    filter_nickname_prefixes = {}

    mention_tracker = False
    mention_threshold = 20
    mention_reset_time = 60


class ModPlugin(Plugin):
    CONFIG_CLS = ModPluginConfig

    def __init__(self, bot, config, *args, **kwargs):
        super(ModPlugin, self).__init__(bot, config or ModPluginConfig(), *args, **kwargs)

        self.mention_tracking = {}
        self.invite_re = re.compile(self.config.filter_invites_re, re.I)

    def action(self, action, entity, reason, *metadata):
        context = ""

        if action is ActionType.DELETE_MSG and entity.guild.can(self.state.me, Permissions.MANAGE_MESSAGES):
            entity.delete()
            context = 'author = `{}`\n mid = `{}` \n message = ```\n{}\n```'.format(
                entity.author, entity.id, entity.content)
        elif action is ActionType.CHANGE_NICK and entity.guild.can(self.state.me, Permissions.MANAGE_NICKNAMES):
            entity.set_nickname(metadata[0])
            context = 'user = `{}`\n before = `{}`\n after = `{}`'.format(entity.user, entity.nick, metadata[0])
        elif action is ActionType.KICK_USER and entity.guild.can(self.state.me, Permissions.KICK_MEMBERS):
            entity.kick()
            context = 'user = `{}`'.format(entity.user)
        elif action is ActionType.BAN_USER and entity.guild.can(self.state.me, Permissions.BAN_MEMBERS):
            entity.ban()
            context = 'user = `{}`'.format(entity.user)

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

        ow = event.channel.create_overwrite(entity, deny=Permissions.READ_MESSAGES)
        event.msg.reply('Blocked {} from viewing this channel'.format(entity))

    @Plugin.listen('GuildMemberUpdate', Plugin.Prio.BEFORE)
    def on_guild_member_update(self, event):
        if not self.config.filter_nicknames:
            return

        pre_member = self.state.guilds[event.member.guild_id].members[event.member.id]

        if event.member.nick and event.member.nick != pre_member.nick:
            nick = event.member.nick

            for word, repl in self.config.filter_nicknames_words.items():
                if word in nick:
                    nick = nick.replace(word, repl)

            for pref, repl in self.config.filter_nicknames_prefixes.items():
                if nick.startswith(pref):
                    nick = nick[len(pref):]

            if nick != event.member.nick:
                self.action(
                    ActionType.CHANGE_NICK,
                    event.member,
                    'matched nickname filter',
                    nick)

    @Plugin.listen('MessageCreate')
    def on_message_create(self, event):
        if not event.message.channel.guild or event.message.author.id == self.state.me.id:
            return

        member = event.message.channel.guild.members.get(event.message.author.id)
        if not member or set(self.config.whitelist_roles) & set(member.roles):
            return

        if self.config.filter_invites:
            self.step_filter_invites(event.message)

        if self.config.mention_tracker:
            self.step_track_mentions(event.message)

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
                        ActionType.KICK_USER,
                        message.member,
                        'mention spam')
                    del self.mention_tracking[message.author.id]
        else:
            self.mention_tracking[message.author.id] = UserMentions(
                    total_mentioned, time.time() + self.config.mention_reset_time)

    def step_filter_invites(self, message):
        matches = list(filter(lambda k: k not in self.config.filter_invites_whitelist,
                self.invite_re.findall(message.content)))

        if not len(matches):
            return

        matches = [i[1] for i in matches]
        self.action(ActionType.DELETE_MSG, message, 'contained invites `{}`'.format(' '.join(matches)))
