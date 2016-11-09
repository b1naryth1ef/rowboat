import functools

from holster.emitter import Priority
from disco.bot import Plugin
from disco.bot.command import CommandLevels
from disco.api.http import APIException

from rowboat import RowboatPlugin
from rowboat.sql import init_db
from rowboat.redis import rdb
from rowboat.types.guild import GuildConfig


HELP_MESSAGE = '''\
:information_source: Info, docs, and detailed help can be found here: <https://github.com/b1naryth1ef/rowboat/wiki>
'''


class CorePlugin(Plugin):
    def load(self, ctx):
        init_db()

        self.guild_configs = ctx.get('guild_configs', {})
        super(CorePlugin, self).load(ctx)

        for plugin in self.bot.plugins.values():
            if not isinstance(plugin, RowboatPlugin):
                continue

            plugin.register_trigger('command', 'pre', functools.partial(self.on_pre, plugin))
            plugin.register_trigger('listener', 'pre', functools.partial(self.on_pre, plugin))

    def unload(self, ctx):
        ctx['guild_configs'] = self.guild_configs
        super(CorePlugin, self).unload(ctx)

    def set_guild_config(self, gid, config):
        self.guild_configs[gid] = config

        # TODO: make better
        for plugin in self.bot.plugins.values():
            if hasattr('on_config_update'):
                plugin.on_config_update(getattr(config.plugins, plugin.name.lower()))

    def on_pre(self, plugin, event, args, kwargs):
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
            cfg = self.guild_configs[event.id] = GuildConfig.load_from_id(event.id)

            # Set nickname on boot
            if cfg.nickname:
                m = event.members.select_one(id=self.state.me.id)
                if m and m.nick != cfg.nickname:
                    try:
                        m.set_nickname(cfg.nickname)
                    except APIException as e:
                        self.log.warning('Failed to set nickname for guild %s (%s)', event.guild, e.content)

    @Plugin.command('reload', '[plugin:str]', group='control', level=CommandLevels.OWNER, oob=True)
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
            self.guild_configs[event.guild.id] = GuildConfig.create_from_url(event.guild.id, url)
            event.msg.reply(':ok_hand: successfully loaded configuration')
        except Exception as e:
            event.msg.reply(':no_entry: {}'.format(e))

    @Plugin.command('reload')
    def command_reload(self, event):
        if not event.guild:
            return

        if event.guild.id not in self.guild_configs:
            return event.msg.reply(':warning: this guild is not setup yet')

        try:
            new = GuildConfig.load_from_id(event.guild.id, fresh=True)
        except Exception as e:
            return event.msg.reply(':no_entry: {}'.format(e))

        self.guild_configs[event.guild.id] = new
        event.msg.reply(':ok_hand: guild configuration reloaded')

    @Plugin.command('help')
    def command_help(self, event):
        # TODO: cooldown
        event.msg.reply(HELP_MESSAGE)

    @Plugin.command('config')
    def command_config(self, event):
        if not event.guild or event.guild.id not in self.guild_configs:
            return

        event.msg.reply('Config URL: <{}>'.format(rdb.get('config:{}'.format(event.guild.id))))
