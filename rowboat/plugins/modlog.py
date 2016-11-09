import yaml
import requests
import operator

from six import BytesIO
from PIL import Image
from holster.enum import Enum
from holster.emitter import Priority
from disco.util.functional import cached_property
from disco.types import UNSET

from rowboat import RowboatPlugin as Plugin
from rowboat.types import ListField, DictField, ChannelField
from rowboat.types.plugin import PluginConfig
from rowboat.plugins.messages import Message

ZERO_WIDTH_SPACE = u'\u200B'

Actions = Enum()


class ModLogConfig(PluginConfig):
    channels = DictField(ChannelField, ListField(Actions))

    @cached_property
    def subscribed(self):
        return (reduce(operator.or_, map(set, self.channels.values())) or set(Actions.attrs))


class ModLogPlugin(Plugin):
    def register_action(self, name, emoji, fmt):
        action = Actions.add(name)
        self.action_emoji[action] = emoji
        self.action_fmt[action] = fmt

    def load(self, ctx):
        if not Actions.attrs:
            self.action_emoji = {}
            self.action_fmt = {}

            with open('data/actions.yaml') as f:
                for k, v in yaml.load(f.read()).items():
                    self.register_action(k, v['emoji'], v['format'])
        else:
            self.action_emoji = ctx['action_emoji']
            self.action_fmt = ctx['action_fmt']

        super(ModLogPlugin, self).load(ctx)

    def unload(self, ctx):
        ctx['action_emoji'] = self.action_emoji
        ctx['action_fmt'] = self.action_fmt
        super(ModLogPlugin, self).unload(ctx)

    def log_action(self, action, event, attachment=None, **details):
        if not {action} & event.config.subscribed:
            return

        emoji = self.action_emoji.get(action, '')
        fmt = self.action_fmt[action]
        msg = ':{}: {}'.format(emoji, fmt.format(e=event, **details))

        for channel, config in event.config.channels.items():
            config = set(config) if config else set(Actions.attrs)
            if not {action} & config:
                continue

            # TODO: consider caching this better
            if not isinstance(channel, int):
                cid = event.guild.channels.select_one(name=channel).id
                cobj = event.guild.channels.get(cid)

            if not cobj:
                self.log.warning('Invalid channel: %s (%s)', channel, cid)
                continue

            cobj.send_message(
                msg.replace('@', '@' + ZERO_WIDTH_SPACE),
                attachment=attachment)

    @Plugin.listen('ChannelCreate')
    def on_channel_create(self, event):
        self.log_action(Actions.CHANNEL_CREATE, event)

    @Plugin.listen('ChannelDelete')
    def on_channel_delete(self, event):
        self.log_action(Actions.CHANNEL_DELETE, event)

    @Plugin.listen('GuildBanAdd')
    def on_guild_ban_add(self, event):
        self.log_action(Actions.GUILD_BAN_ADD, event)

    @Plugin.listen('GuildBanRemove')
    def on_guild_ban_remove(self, event):
        self.log_action(Actions.GUILD_BAN_REMOVE, event)

    @Plugin.listen('GuildMemberAdd')
    def on_guild_member_add(self, event):
        self.log_action(Actions.GUILD_MEMBER_ADD, event)

    @Plugin.listen('GuildMemberRemove')
    def on_guild_member_remove(self, event):
        self.log_action(Actions.GUILD_MEMBER_REMOVE, event)

    @Plugin.listen('GuildRoleCreate')
    def on_guild_role_create(self, event):
        self.log_action(Actions.GUILD_ROLE_CREATE, event)

    @Plugin.listen('GuildRoleDelete', priority=Priority.BEFORE)
    def on_guild_role_delete(self, event):
        pre_role = event.guild.roles.get(event.role_id)
        self.log_action(Actions.GUILD_ROLE_DELETE, event, pre_role=pre_role)

    @Plugin.listen('GuildMemberUpdate', priority=Priority.BEFORE)
    def on_guild_member_update(self, event):
        pre_member = event.guild.members.get(event.id)

        if pre_member.nick != event.nick:
            self.log_action(
                Actions.CHANGE_NICK,
                event,
                before=pre_member.nick or '<NO_NICK>',
                after=event.nick or '<NO_NICK>')

        if pre_member.user.username != event.user.username:
            self.log_action(
                Actions.CHANGE_USERNAME,
                event,
                before=pre_member.user.username,
                after=event.user.username)

        pre_roles = set(pre_member.roles)
        post_roles = set(event.roles)
        if pre_roles != post_roles:
            added = post_roles - pre_roles
            removed = pre_roles - post_roles

            for role in filter(bool, map(event.guild.roles.get, added)):
                self.log_action(Actions.GUILD_MEMBER_ROLES_ADD, event, role=role)

            for role in filter(bool, map(event.guild.roles.get, removed)):
                self.log_action(Actions.GUILD_MEMBER_ROLES_RMV, event, role=role)

    @Plugin.listen('PresenceUpdate', priority=Priority.BEFORE)
    def on_presence_update(self, event):
        """
        TODO:
            optimize so we only DL images once per guild
        """
        if Actions.GUILD_MEMBER_AVATAR_CHANGE not in event.config.subscribed:
            return

        pre_member = event.guild.members.get(event.user.id)
        if not pre_member:
            return

        if event.user.avatar is UNSET:
            return

        if pre_member.user.avatar != event.user.avatar:
            image = Image.new('RGB', (256, 128))

            if pre_member.user.avatar:
                r = requests.get(pre_member.user.avatar_url)
                image.paste(Image.open(BytesIO(r.content)), (0, 0))

            if event.user.avatar:
                r = requests.get(event.user.avatar_url)
                image.paste(Image.open(BytesIO(r.content)), (128, 0))

            combined = BytesIO()
            image.save(combined, 'jpeg', quality=55)
            combined.seek(0)
            self.log_action(Actions.GUILD_MEMBER_AVATAR_CHANGE, event, attachment=('avatar.jpg', combined))

    @Plugin.listen('MessageUpdate', priority=Priority.BEFORE)
    def on_message_update(self, event):
        try:
            msg = Message.get(Message.id == event.id)
        except Message.DoesNotExist:
            return

        if not event.channel or not event.author:
            return

        if msg.content != event.content:
            self.log_action(Actions.MESSAGE_EDIT, event, before=msg.content, after=event.content)

    @Plugin.listen('MessageDelete')
    def on_message_delete(self, event):
        try:
            msg = Message.get(Message.id == event.id)
        except Message.DoesNotExist:
            return

        channel = self.state.channels.get(msg.channel_id)
        if not channel or not msg.author:
            return

        self.log_action(Actions.MESSAGE_DELETE, event,
                author=msg.author,
                author_id=msg.author.id,
                channel=channel,
                msg=msg.content)
