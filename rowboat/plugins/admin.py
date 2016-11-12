from disco.bot import CommandLevels

from rowboat import RowboatPlugin as Plugin
from rowboat.types.plugin import PluginConfig


class AdminConfig(PluginConfig):
    pass


class AdminPlugin(Plugin):
    @Plugin.command('kick', '<user:user> [reason:str...]', level=CommandLevels.MOD)
    def kick(self, event, user, reason=None):
        """
        Kick a user from the server (with an optional reason for the modlog).
        """

        u = event.guild.get_member(user)
        if u:
            self.bot.plugins.get('ModLogPlugin').create_debounce(event, user, 'kick',
                actor=str(event.author),
                reason=reason or 'no reason')
            u.kick()
        else:
            event.msg.reply(':warning: Invalid user!')

    @Plugin.command('ban', '<user:user> [reason:str...]', level=CommandLevels.MOD)
    def ban(self, event, user, reason=None):
        """
        Ban a user from the server (with an optional reason for the modlog).
        """

        u = event.guild.get_member(user)
        if u:
            self.bot.plugins.get('ModLogPlugin').create_debounce(event, user, 'ban_reason',
                actor=str(event.author),
                reason=reason or 'no reason')
            u.ban()
        else:
            event.msg.reply(':warning: Invalid user!')
