import redis
import requests
import functools

from holster.emitter import Priority

from disco.bot import Plugin
from disco.bot.command import CommandLevels
from rowboat.types.guild import GuildConfig


HELP_MESSAGE = '''\
:information_source: Info, docs, and detailed help can be found here: <https://github.com/b1naryth1ef/rowboat/wiki>
'''


class CorePlugin(Plugin):
    def load(self, ctx):
        self.db = redis.Redis(db=11)
        self.guild_configs = ctx.get('guild_configs', {})
        super(CorePlugin, self).load(ctx)

        for plugin in self.bot.plugins.values():
            if plugin == self:
                continue

            plugin.register_trigger('command', 'pre', functools.partial(self.on_pre, plugin))
            plugin.register_trigger('listener', 'pre', functools.partial(self.on_pre, plugin))

    def unload(self, ctx):
        ctx['guild_configs'] = self.guild_configs
        super(CorePlugin, self).unload(ctx)

    @Plugin.listen('GuildCreate', priority=Priority.BEFORE, conditional=lambda e: not e.created)
    def on_guild_create(self, event):
        if self.db.sismember('guilds', event.id):
            self.log.info('Loading configuration for guild %s', event.id)
            self.load_guild_config(event.id)

    def load_guild_config(self, id, fresh=False):
        if self.db.exists('config:cached:{}'.format(id)) and not fresh:
            cfg = GuildConfig.loads(self.db.get('config:cached:{}'.format(id)), safe=True)
        else:
            url = self.db.get('config:{}'.format(id))
            try:
                r = requests.get(url, timeout=15)
                r.raise_for_status()
            except:
                self.log.warning('Failed to load config for guild {}'.format(id))
                return

            try:
                cfg = GuildConfig.loads(r.content, safe=False)
                self.db.set('config:cached:{}'.format(id), r.content)
            except:
                self.log.warning('Failed to parse config for guild {}'.format(id))
                return

        self.guild_configs[id] = cfg

    def on_pre(self, plugin, event, args, kwargs):
        if not event.guild:
            return

        if event.guild.id not in self.guild_configs:
            return

        plugin = plugin.name.lower().replace('plugin', '')
        if not getattr(self.guild_configs[event.guild.id].plugins, plugin, None):
            return

        event.config = getattr(self.guild_configs[event.guild.id].plugins, plugin)
        return event

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
            return

        if event.guild.id in self.guild_configs:
            return event.msg.reply(':warning: this guild is already setup')

        if not event.guild.owner_id == event.author.id:
            return event.msg.reply(':warning: only the guild owner can setup rowboat')

        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            cfg = GuildConfig.loads(r.content, safe=False)
            self.guild_configs[event.guild.id] = cfg
            self.db.sadd('guilds', event.guild.id)
            self.db.set('config:{}'.format(event.guild.id), url)
            self.db.set('config:cached:{}'.format(event.guild.id), r.content)
            event.msg.reply(':ok_hand: successfully loaded configuration')
        except Exception as e:
            event.msg.reply('Error loading configuration: {}'.format(e))

    @Plugin.command('reload')
    def command_reload(self, event):
        if not event.guild:
            return

        if event.guild.id not in self.guild_configs:
            return event.msg.reply(':warning: this guild is not setup yet')

        self.load_guild_config(event.guild.id, fresh=True)
        event.msg.reply(':ok_hand: guild configuration reloaded')

    @Plugin.command('help')
    def command_help(self, event):
        # TODO: cooldown
        event.msg.reply(HELP_MESSAGE)

    @Plugin.command('config')
    def command_config(self, event):
        if not event.guild or event.guild.id not in self.guild_configs:
            return

        event.msg.reply('Config URL: <{}>'.format(
            self.db.get('config:{}'.format(event.guild.id))))
