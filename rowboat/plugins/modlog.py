from disco.bot import Plugin

from holster.enum import Enum
from holster.emitter import Priority

from rowboat.types import Field, channel
from rowboat.types.plugin import PluginConfig


Actions = Enum(
    'CHANNEL_CREATE',
    'CHANNEL_DELETE',
    'GUILD_BAN_ADD',
    'GUILD_BAN_REMOVE',
    'GUILD_MEMBER_ADD',
    'GUILD_MEMBER_REMOVE',
    'GUILD_ROLE_CREATE',
    'GUILD_ROLE_DELETE',
    'CHANGE_NICK',
    'CHANGE_USERNAME',
)

ACTION_DATA = {
    Actions.CHANNEL_CREATE: (':heavy_plus_sign:', 'channel {e.mention} (`{e.id}`) was created'),
    Actions.CHANNEL_DELETE: (':heavy_minus_sign', 'channel #{e.name} (`{e.id}`) was deleted'),
    Actions.GUILD_BAN_ADD: (':no_entry_sign:', 'user {e} (`{e.id}`) was banned'),
    Actions.GUILD_BAN_REMOVE: (':white_check_mark:', 'user {e} (`{e.id}`) was unbanned'),
    Actions.GUILD_MEMBER_ADD: (':heavy_plus_sign:', 'user {e} (`{e.id}`) joined the server'),
    Actions.GUILD_MEMBER_REMOVE: (':heavy_minus_sign:', 'user {e} (`{e.id}`) left the server'),
    Actions.GUILD_ROLE_CREATE: (':heavy_plus_sign:', 'role {e.role} (`{e.id}`) was created'),
    Actions.GUILD_ROLE_DELETE: (':heavy_minus_sign:', 'role {pre_role} (`{e.role_id}`) was deleted'),
    Actions.CHANGE_NICK:
        (':pencil:', 'user {e.user} (`{e.user.id}`) changed their nick from `{before}` to `{after}`'),
    Actions.CHANGE_USERNAME:
        (':pencil:', 'user {e.user} (`{e.user.id}`) changed their username from `{before}` to `{after}`'),
}


class ModLogConfig(PluginConfig):
    channel = Field(channel)

    channel_create = Field(bool, default=True)
    channel_delete = Field(bool, default=True)
    guild_ban_add = Field(bool, default=True)
    guild_ban_remove = Field(bool, default=True)
    guild_member_add = Field(bool, default=True)
    guild_member_remove = Field(bool, default=True)
    guild_role_create = Field(bool, default=True)
    guild_role_delete = Field(bool, default=True)
    change_nick = Field(bool, default=True)
    change_username = Field(bool, default=True)


class ModLogPlugin(Plugin):
    def log_action(self, action, event, **details):
        if not getattr(event.config, action.name.lower(), False):
            return

        emoji, fmt = ACTION_DATA[action]
        msg = '{} {}'.format(emoji, fmt.format(e=event, **details))

        if not isinstance(event.config.channel, int):
            event.config.channel = event.guild.channels.select_one(name=event.config.channel).id

        channel = event.guild.channels[event.config.channel]
        channel.send_message(msg)

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
        if set(pre_member.roles) != set(event.roles):
            # TODO: calculate diff and emit add/remove events
            pass
