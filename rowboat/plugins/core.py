import humanize
import functools

from datetime import datetime
from holster.emitter import Priority
from disco.bot import Plugin
from disco.api.http import APIException
from disco.bot.command import CommandEvent, CommandLevels

from rowboat import RowboatPlugin, VERSION
from rowboat.models.guild import Guild
from rowboat.sql import init_db
from rowboat.redis import rdb

INFO_MESSAGE = '''\
:information_source: Rowboat V{} - more information and detailed help can be found here:\
<https://github.com/b1naryth1ef/rowboat/wiki>
'''.format(VERSION)


class CorePlugin(Plugin):
    def load(self, ctx):
        init_db()

        self.startup = ctx.get('startup', datetime.utcnow())
        self.guilds = ctx.get('guilds', {})
        super(CorePlugin, self).load(ctx)

        for plugin in self.bot.plugins.values():
            if not isinstance(plugin, RowboatPlugin):
                continue

            plugin.register_trigger('command', 'pre', functools.partial(self.on_pre, plugin))
            plugin.register_trigger('listener', 'pre', functools.partial(self.on_pre, plugin))

    def unload(self, ctx):
        ctx['guilds'] = self.guilds
        ctx['startup'] = self.startup
        super(CorePlugin, self).unload(ctx)

    def on_pre(self, plugin, func, event, args, kwargs):
        if isinstance(event, CommandEvent):
            if event.command.metadata.get('global_', False):
                return event
        elif hasattr(func, 'subscriptions'):
            if func.subscriptions[0].metadata.get('global_', False):
                return event

        if hasattr(event, 'guild'):
            guild_id = event.guild.id
        elif hasattr(event, 'guild_id'):
            guild_id = event.guild_id
        else:
            return

        if guild_id not in self.guilds:
            return

        config = self.guilds[guild_id].get_config()

        plugin_name = plugin.name.lower().replace('plugin', '')
        if not getattr(config.plugins, plugin_name, None):
            return

        if plugin.whitelisted and plugin_name not in self.guilds[guild_id].whitelist:
            return

        event.config = getattr(config.plugins, plugin_name)
        return event

    @Plugin.listen('GuildCreate', priority=Priority.BEFORE, conditional=lambda e: not e.created)
    def on_guild_create(self, event):
        try:
            guild = Guild.with_id(event.id)
        except Guild.DoesNotExist:
            self.log.warning('Guild {} is not setup'.format(event.id))
            return

        self.guilds[event.id] = guild

        if guild.get_config().nickname:
            def set_nickname():
                m = event.members.select_one(id=self.state.me.id)
                if m and m.nick != guild.get_config().nickname:
                    try:
                        m.set_nickname(guild.get_config().nickname)
                    except APIException as e:
                        self.log.warning('Failed to set nickname for guild %s (%s)', event.guild, e.content)
            self.spawn_later(5, set_nickname)

    @Plugin.listen('MessageCreate')
    def on_message_create(self, event):
        if event.message.author.id == self.client.state.me.id:
            return

        if hasattr(event, 'guild') and event.guild:
            guild_id = event.guild.id
        elif hasattr(event, 'guild_id') and event.guild_id:
            guild_id = event.guild_id
        else:
            return

        guild = self.guilds.get(guild_id)
        config = guild and guild.get_config()
        if config:
            if config.commands:
                commands = list(self.bot.get_commands_for_message(
                    config.commands.mention,
                    {},
                    config.commands.prefix,
                    event.message))
        else:
            commands = list(self.bot.get_commands_for_message(True, {}, '', event.message))

        if not len(commands):
            return

        user_level = 0
        if config:
            for oid in event.guild.get_member(event.author).roles:
                if oid in config.levels and config.levels[oid] > user_level:
                    user_level = config.levels[oid]

            # User ID overrides should override all others
            if event.author.id in config.levels:
                user_level = config.levels[event.author.id]

        global_admin = rdb.sismember('global_admins', event.author.id)

        for command, match in commands:
            if command.level == -1 and not global_admin:
                continue

            level = command.level

            if not config and command.triggers[0] != 'setup':
                continue
            elif config and config.commands and command.plugin != self:
                if command.triggers[0] in config.commands.overrides:
                    override = config.commands.overrides[command.triggers[0]]
                    if override.disabled:
                        continue

                    if override.level is not None:
                        level = override.level

            if not global_admin and user_level < level:
                continue

            command.plugin.execute(CommandEvent(command, event.message, match))
            return

    @Plugin.command('reload', '[plugin:str]', group='control', level=-1, oob=True)
    def command_control_reload(self, event, plugin=None):
        if not plugin:
            for plugin in self.bot.plugins.values():
                plugin.reload()
            return event.msg.reply(':recycle: reloaded all plugins')
        self.bot.plugins.get(plugin).reload()
        event.msg.reply(':recycle: reloaded plugin `{}`'.format(plugin))

    @Plugin.command('wl add', '<plugin:str> [guild:snowflake]', group='control', level=-1)
    def control_whitelist_add(self, event, plugin, guild=None):
        guild = self.guilds.get(guild or event.guild.id)
        if not guild:
            return event.msg.reply(':warning: this guild isnt setup yet')

        guild.whitelist.append(plugin)
        guild.save()
        event.msg.reply(':ok_hand: this guild has been whitelisted for {}'.format(plugin))

    @Plugin.command('wl rmv', '<plugin:str> [guild:snowflake]', group='control', level=-1)
    def control_whitelist_rmv(self, event, plugin, guild=None):
        guild = self.guilds.get(guild or event.guild.id)
        if not guild:
            return event.msg.reply(':warning: this guild isnt setup yet')

        if plugin not in guild.whitelist:
            return event.msg.reply(':warning: this guild isnt whitelisted for {}'.format(plugin))

        guild.whitelist.remove(plugin)
        guild.save()
        event.msg.reply(':ok_hand: this guild has been unwhitelisted for {}'.format(plugin))

    @Plugin.command('wl list', '[guild:snowflake]', group='control', level=-1)
    def control_whitelist_list(self, event, guild=None):
        guild = self.guilds.get(guild or event.guild.id)
        if not guild:
            return event.msg.reply(':warning: this guild isnt setup yet')

        event.msg.reply('`{}`'.format(', '.join(guild.whitelist)))

    @Plugin.command('setup', '<url:str>')
    def command_setup(self, event, url):
        if not event.guild:
            return event.msg.reply(':warning: this command can only be used in servers')

        # Make sure we're not already setup
        if event.guild.id in self.guilds:
            return event.msg.reply(':warning: this server is already setup')

        global_admin = rdb.sismember('global_admins', event.author.id)

        # Make sure this is the owner of the server
        if not global_admin:
            if not event.guild.owner_id == event.author.id:
                return event.msg.reply(':warning: only the server owner can setup rowboat')

        # Make sure we have admin perms
        m = event.guild.members.select_one(id=self.state.me.id)
        if not m.permissions.administrator and not global_admin:
            return event.msg.reply(':warning: bot must have the Administrator permission')

        try:
            guild = Guild.create_from_url(event.guild.id, url)
            self.guilds[event.guild.id] = guild
            event.msg.reply(':ok_hand: successfully loaded configuration')
        except Exception as e:
            raise
            event.msg.reply(':no_entry: {}'.format(e))

    @Plugin.command('reload', level=CommandLevels.ADMIN)
    def command_reload(self, event):
        if not event.guild:
            return

        guild = self.guilds.get(event.guild.id)
        if not guild:
            return event.msg.reply(':warning: this guild is not setup yet')

        try:
            guild.reload()
        except Exception as e:
            raise
            return event.msg.reply(':no_entry: {}'.format(e))

        event.msg.reply(':ok_hand: guild configuration reloaded')

    @Plugin.command('about', level=CommandLevels.ADMIN)
    def command_help(self, event):
        event.msg.reply(INFO_MESSAGE)

    @Plugin.command('config', level=CommandLevels.ADMIN)
    def command_config(self, event):
        if not event.guild or event.guild.id not in self.guilds:
            return

        event.msg.reply('Current configuration URL: <{}>'.format(self.guilds[event.guild.id].config_url))

    @Plugin.command('uptime', level=-1)
    def command_uptime(self, event):
        event.msg.reply('Rowboat was started {}'.format(
            humanize.naturaltime(datetime.utcnow() - self.startup)
        ))
