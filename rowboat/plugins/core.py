import humanize
import functools

from datetime import datetime
from holster.emitter import Priority
from disco.bot import Plugin
from disco.api.http import APIException
from disco.bot.command import CommandEvent, CommandLevels

from rowboat import RowboatPlugin, VERSION
from rowboat.sql import init_db
from rowboat.redis import rdb
from rowboat.types.guild import GuildConfig

INFO_MESSAGE = '''\
:information_source: Rowboat V{} - more information and detailed help can be found here:\
<https://github.com/b1naryth1ef/rowboat/wiki>
'''.format(VERSION)


class CorePlugin(Plugin):
    def load(self, ctx):
        init_db()

        self.startup = ctx.get('startup', datetime.utcnow())
        self.guild_configs = ctx.get('guild_configs', {})
        super(CorePlugin, self).load(ctx)

        for plugin in self.bot.plugins.values():
            if not isinstance(plugin, RowboatPlugin):
                continue

            plugin.register_trigger('command', 'pre', functools.partial(self.on_pre, plugin))
            plugin.register_trigger('listener', 'pre', functools.partial(self.on_pre, plugin))

    def unload(self, ctx):
        ctx['guild_configs'] = self.guild_configs
        ctx['startup'] = self.startup
        super(CorePlugin, self).unload(ctx)

    def set_guild_config(self, gid, config):
        self.guild_configs[gid] = config

        # TODO: make better
        for plugin in self.bot.plugins.values():
            if hasattr('on_config_update'):
                plugin.on_config_update(getattr(config.plugins, plugin.name.lower()))

    def on_pre(self, plugin, event, args, kwargs):
        if isinstance(event, CommandEvent):
            if event.command.metadata.get('global_', False):
                return event

        if hasattr(event, 'guild'):
            guild_id = event.guild.id
        elif hasattr(event, 'guild_id'):
            guild_id = event.guild_id
        else:
            return

        if guild_id not in self.guild_configs:
            return

        plugin = plugin.name.lower().replace('plugin', '')
        if not getattr(self.guild_configs[guild_id].plugins, plugin, None):
            return

        event.config = getattr(self.guild_configs[guild_id].plugins, plugin)
        return event

    @Plugin.listen('GuildCreate', priority=Priority.BEFORE, conditional=lambda e: not e.created)
    def on_guild_create(self, event):
        if rdb.sismember('guilds', event.id):
            self.log.info('Loading configuration for guild %s', event.id)
            cfg, leftover = GuildConfig.load_from_id(event.id)
            self.guild_configs[event.id] = cfg

            # Set nickname on boot
            if cfg.nickname:
                m = event.members.select_one(id=self.state.me.id)
                if m and m.nick != cfg.nickname:
                    try:
                        m.set_nickname(cfg.nickname)
                    except APIException as e:
                        self.log.warning('Failed to set nickname for guild %s (%s)', event.guild, e.content)

    @Plugin.listen('MessageCreate')
    def on_message_create(self, event):
        if event.message.author.id == self.client.state.me.id:
            return

        if hasattr(event, 'guild'):
            guild_id = event.guild.id
        elif hasattr(event, 'guild_id'):
            guild_id = event.guild_id
        else:
            return

        if guild_id not in self.guild_configs:
            return

        config = self.guild_configs[guild_id]

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
        for oid in [event.author.id] + event.guild.get_member(event.author).roles:
            if oid in config.levels and config.levels[oid] > user_level:
                user_level = config.levels[oid]

        global_admin = rdb.sismember('global_admins', event.author.id)

        for command, match in commands:
            if command.level == -1 and not global_admin:
                continue

            level = command.level

            if config.commands and command.plugin != self:
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

    @Plugin.command('setup', '<url:str>')
    def command_setup(self, event, url):
        if not event.guild:
            return event.msg.reply(':warning: this command can only be used in servers')

        # Make sure we're not already setup
        if event.guild.id in self.guild_configs:
            return event.msg.reply(':warning: this server is already setup')

        # Make sure this is the owner of the server
        if not rdb.sismember('global_admins', event.author.id):
            if not event.guild.owner_id == event.author.id:
                return event.msg.reply(':warning: only the server owner can setup rowboat')

        # Make sure we have admin perms
        m = event.guild.members.select_one(id=self.state.me.id)
        if not m.permissions.administrator:
            return event.msg.reply(':warning: bot must have the Administrator permission')

        try:
            self.guild_configs[event.guild.id], leftover = GuildConfig.create_from_url(event.guild.id, url)
            event.msg.reply(':ok_hand: successfully loaded configuration')

            if leftover:
                event.msg.reply(':warning: your config had the following leftover (e.g. invalid) keys: `{}`'.format(leftover))
        except Exception as e:
            event.msg.reply(':no_entry: {}'.format(e))

    @Plugin.command('reload', level=CommandLevels.ADMIN)
    def command_reload(self, event):
        if not event.guild:
            return

        if event.guild.id not in self.guild_configs:
            return event.msg.reply(':warning: this guild is not setup yet')

        try:
            new, leftover = GuildConfig.load_from_id(event.guild.id, fresh=True)
        except Exception as e:
            return event.msg.reply(':no_entry: {}'.format(e))

        self.guild_configs[event.guild.id] = new
        event.msg.reply(':ok_hand: guild configuration reloaded')
        if leftover:
            event.msg.reply(':warning: your config had the following leftover (e.g. invalid) keys: `{}`'.format(leftover))

    @Plugin.command('info', level=CommandLevels.ADMIN)
    def command_help(self, event):
        event.msg.reply(INFO_MESSAGE)

    @Plugin.command('config', level=CommandLevels.ADMIN)
    def command_config(self, event):
        if not event.guild or event.guild.id not in self.guild_configs:
            return

        event.msg.reply('Config URL: <{}>'.format(rdb.get('config:{}'.format(event.guild.id))))

    @Plugin.command('uptime', level=-1)
    def command_uptime(self, event):
        event.msg.reply('Rowboat was started {}'.format(
            humanize.naturaltime(datetime.utcnow() - self.startup)
        ))
