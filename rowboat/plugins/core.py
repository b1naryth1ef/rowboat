import os
import time
import pprint
import humanize
import functools
import subprocess

from datetime import datetime, timedelta
from holster.emitter import Priority
from disco.api.http import APIException
from disco.bot.command import CommandEvent, CommandLevels

from rowboat import BasePlugin as Plugin
from rowboat import RowboatPlugin, VERSION
from rowboat.sql import init_db
from rowboat.redis import rdb
from rowboat.models.guild import Guild
from rowboat.plugins.modlog import Actions

ENV = os.getenv('ENV', 'local')

PY_CODE_BLOCK = u'```py\n{}\n```'

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

        if hasattr(event, 'guild') and event.guild:
            guild_id = event.guild.id
        elif hasattr(event, 'guild_id') and event.guild_id:
            guild_id = event.guild_id
        else:
            return

        if guild_id not in self.guilds:
            return

        event.base_config = self.guilds[guild_id].get_config()

        plugin_name = plugin.name.lower().replace('plugin', '')
        if not getattr(event.base_config.plugins, plugin_name, None):
            return

        if plugin.whitelisted and plugin_name not in self.guilds[guild_id].whitelist:
            return

        event.config = getattr(event.base_config.plugins, plugin_name)
        return event

    @Plugin.schedule(290)
    def update_guild_bans(self):
        # TODO: disco calls this function right away, need guilds to be synced
        time.sleep(10)

        to_update = [
            guild for guild in Guild.select().where(
                (Guild.last_ban_sync < (datetime.utcnow() - timedelta(days=1))) |
                (Guild.last_ban_sync >> None)
            )
            if guild.guild_id in self.client.state.guilds]

        # Update 10 at a time
        for guild in to_update[:10]:
            guild.sync_bans(self.client.state.guilds.get(guild.guild_id))

    def send_control_message(self, content, *args, **kwargs):
        chan = self.client.state.dms.find_one(lambda dm: 80351110224678912 in dm.recipients)
        if not chan:
            self.log.warning('Failed to find control channel')
            return

        chan.send_message(u'[{}] {}'.format(ENV, content), *args, **kwargs)

    @Plugin.listen('Resumed')
    def on_resumed(self, event):
        self.send_control_message(u'Resumed: {}'.format(', '.join(event.trace)))

    @Plugin.listen('Ready')
    def on_ready(self, event):
        self.send_control_message(u'Connected: {}'.format(', '.join(event.trace)))

    @Plugin.listen('GuildCreate', priority=Priority.BEFORE, conditional=lambda e: not e.created)
    def on_guild_create(self, event):
        try:
            guild = Guild.with_id(event.id)
        except Guild.DoesNotExist:
            return

        # Ensure we're updated
        guild.sync(event.guild)

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
        # Ignore messages sent by bots
        if event.message.author.bot:
            return

        if rdb.sismember('ignored_channels', event.message.channel_id):
            return

        # If this is message for a guild, grab the guild object
        if hasattr(event, 'guild') and event.guild:
            guild_id = event.guild.id
        elif hasattr(event, 'guild_id') and event.guild_id:
            guild_id = event.guild_id
        else:
            guild_id = None

        guild = self.guilds.get(event.guild.id) if guild_id else None
        config = guild and guild.get_config()

        # If the guild has configuration, use that (otherwise use defaults)
        if config:
            if config.commands:
                commands = list(self.bot.get_commands_for_message(
                    config.commands.mention,
                    {},
                    config.commands.prefix,
                    event.message))
        elif guild_id:
            # Otherwise, default to requiring mentions
            commands = list(self.bot.get_commands_for_message(True, {}, '', event.message))
        else:
            if ENV != 'prod':
                if not event.message.content.startswith(ENV + '!'):
                    return
                event.message.content = event.message.content[len(ENV) + 1:]

            # DM's just use the commands (no prefix/mention)
            commands = list(self.bot.get_commands_for_message(False, {}, '', event.message))

        # If we didn't find any matching commands, return
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

        # Grab whether this user is a global admin
        # TODO: cache this
        global_admin = rdb.sismember('global_admins', event.author.id)

        # Iterate over commands and find a match
        for command, match in commands:
            if command.level == -1 and not global_admin:
                continue

            level = command.level

            if guild and not config and command.triggers[0] != 'setup':
                continue
            elif config and config.commands and command.plugin != self:
                if command.triggers[0] in config.commands.overrides or '*' in config.commands.overrides:
                    override = config.commands.overrides.get(
                        command.triggers[0],
                        config.commands.overrides.get('*'))
                    if override.disabled:
                        continue

                    if override.level is not None:
                        level = override.level

            if not global_admin and user_level < level:
                continue

            command.plugin.execute(CommandEvent(command, event.message, match))

            # Dispatch the command used modlog event
            if config:
                event.config = getattr(config.plugins, 'modlog', None)
                if not event.config:
                    return

                plugin = self.bot.plugins.get('ModLogPlugin')
                if plugin:
                    plugin.log_action(Actions.COMMAND_USED, event)

            return

    @Plugin.command('reload', '[plugin:str]', group='control', level=-1, oob=True)
    def command_control_reload(self, event, plugin=None):
        if not plugin:
            for plugin in self.bot.plugins.values():
                plugin.reload()
            return event.msg.reply(':recycle: reloaded all plugins')
        self.bot.plugins.get(plugin).reload()
        event.msg.reply(':recycle: reloaded plugin `{}`'.format(plugin))

    @Plugin.command('update', group='control', level=-1, oob=True)
    def commnad_update(self, event):
        proc = subprocess.Popen(['git', 'pull', 'origin', 'master'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)

        if proc.returncode != 0:
            return event.msg.reply(':warning: failed to update: ```{}```'.format(proc.stderr.read()))

        for plugin in self.bot.plugins.values():
            plugin.reload()
        event.msg.reply(':ok_hand: updated: ```{}```'.format(proc.stdout.read()))

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
            guild = Guild.create_from_url(event.guild, url)
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

    @Plugin.command('eval', level=-1)
    def command_eval(self, event):
        ctx = {
            'bot': self.bot,
            'client': self.bot.client,
            'state': self.bot.client.state,
            'event': event,
            'msg': event.msg,
            'guild': event.msg.guild,
            'channel': event.msg.channel,
            'author': event.msg.author
        }

        # Mulitline eval
        src = event.codeblock
        if src.count('\n'):
            lines = filter(bool, src.split('\n'))
            if lines[-1] and 'return' not in lines[-1]:
                lines[-1] = 'return ' + lines[-1]
            lines = '\n'.join('    ' + i for i in lines)
            code = 'def f():\n{}\nx = f()'.format(lines)
            local = {}

            try:
                exec compile(code, '<eval>', 'exec') in ctx, local
            except Exception as e:
                event.msg.reply(PY_CODE_BLOCK.format(type(e).__name__ + ': ' + str(e)))
                return

            event.msg.reply(PY_CODE_BLOCK.format(pprint.pformat(local['x'])))
        else:
            try:
                result = eval(src, ctx)
            except Exception as e:
                event.msg.reply(PY_CODE_BLOCK.format(type(e).__name__ + ': ' + str(e)))
                return

            event.msg.reply(PY_CODE_BLOCK.format(result))
