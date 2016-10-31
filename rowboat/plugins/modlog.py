import json
import yaml
import operator

from disco.bot import Plugin
from disco.util.functional import cached_property
from holster.enum import Enum
from holster.emitter import Priority

from rowboat.redis import db
from rowboat.types import ListField, DictField, ChannelField
from rowboat.types.plugin import PluginConfig

# 7 days
MESSAGE_CACHE_TIME = 60 * 60 * 24 * 7

Actions = Enum()


class ModLogConfig(PluginConfig):
    channels = DictField(ChannelField, ListField(Actions, test=1))

    @cached_property
    def subscribed(self):
        return (reduce(operator.or_, map(set, self.channels.values())) or Actions.attrs)


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

    def log_action(self, action, event, **details):
        if not {action} & event.config.subscribed:
            return

        emoji = self.action_emoji.get(action, '')
        fmt = self.action_fmt[action]
        msg = ':{}: {}'.format(emoji, fmt.format(e=event, **details))

        for channel, config in event.config.channels.items():
            config = set(config) if config else Actions.attrs
            if not {action} & config:
                continue

            # TODO: consider caching this better
            if not isinstance(channel, int):
                cid = event.guild.channels.select_one(name=channel).id
                cobj = event.guild.channels.get(cid)

            if not cobj:
                self.log.warning('Invalid channel: %s (%s)', channel, cid)
                continue

            cobj.send_message(msg)

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
        print event.role.name
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

        # TODO: avatar change

        if set(pre_member.roles) != set(event.roles):
            # TODO: calculate diff and emit add/remove events
            pass

    @Plugin.listen('MessageCreate')
    def on_message_create(self, event):
        if not {Actions.MESSAGE_EDIT, Actions.MESSAGE_DELETE} & event.config.subscribed:
            return

        db.setex('messages:{}'.format(event.id), json.dumps([
            str(event.author), event.author.id, event.channel.id, event.content, event.pinned
        ]),  MESSAGE_CACHE_TIME)

    @Plugin.listen('MessageUpdate')
    def on_message_update(self, event):
        if not {Actions.MESSAGE_EDIT} & event.config.subscribed:
            return

        if not db.exists('messages:{}'.format(event.id)):
            return

        content = json.loads(db.get('messages:{}'.format(event.id)))[3]

        if content != event.content:
            self.log_action(Actions.MESSAGE_EDIT, event, before=content, after=event.content)

    @Plugin.listen('MessageDelete')
    def on_message_delete(self, event):
        if not {Actions.MESSAGE_DELETE} & event.config.subscribed:
            return

        if not db.exists('messages:{}'.format(event.id)):
            return

        data = json.loads(db.get('messages:{}'.format(event.id)))

        channel = self.state.channels.get(data[2])

        self.log_action(Actions.MESSAGE_DELETE, event,
                author=data[0],
                author_id=data[1],
                channel=channel,
                msg=data[3])
