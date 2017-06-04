import os
import json
import pprint
import signal
import inspect
import humanize
import functools
import contextlib

from datetime import datetime, timedelta
from holster.emitter import Priority
from disco.bot import Bot
from disco.types.message import MessageEmbed
from disco.api.http import APIException
from disco.bot.command import CommandEvent
from disco.util.sanitize import S

from rowboat import ENV
from rowboat.util import LocalProxy
from rowboat.util.stats import timed
from rowboat.plugins import BasePlugin as Plugin
from rowboat.plugins import CommandResponse
from rowboat.sql import init_db
from rowboat.redis import rdb

import rowboat.models
from rowboat.models.user import Infraction
from rowboat.models.guild import Guild, GuildBan
from rowboat.models.message import Command
from rowboat.models.notification import Notification
from rowboat.plugins.modlog import Actions

PY_CODE_BLOCK = u'```py\n{}\n```'

BOT_INFO = '''
Rowboat is a moderation and utilitarian Bot built for large Discord servers.
'''

GREEN_TICK_EMOJI = 'green_tick:305231298799206401'
RED_TICK_EMOJI = 'red_tick:305231335512080385'


class CorePlugin(Plugin):
    def load(self, ctx):
        init_db(ENV)

        self.startup = ctx.get('startup', datetime.utcnow())
        self.guilds = ctx.get('guilds', {})

        super(CorePlugin, self).load(ctx)

        # Overwrite the main bot instances plugin loader so we can magicfy events
        self.bot.add_plugin = self.our_add_plugin

        if ENV != 'prod':
            self.spawn(self.wait_for_plugin_changes)

        self.spawn(self.wait_for_actions)

    def our_add_plugin(self, cls, *args, **kwargs):
        if getattr(cls, 'global_plugin', False):
            Bot.add_plugin(self.bot, cls, *args, **kwargs)
            return

        inst = cls(self.bot, None)
        inst.register_trigger('command', 'pre', functools.partial(self.on_pre, inst))
        inst.register_trigger('listener', 'pre', functools.partial(self.on_pre, inst))
        Bot.add_plugin(self.bot, inst, *args, **kwargs)

    def wait_for_plugin_changes(self):
        import gevent_inotifyx as inotify

        fd = inotify.init()
        inotify.add_watch(fd, 'rowboat/plugins/', inotify.IN_MODIFY)
        while True:
            events = inotify.get_events(fd)
            for event in events:
                # Can't reload core.py sadly
                if event.name == 'core.py':
                    continue

                plugin_name = '{}Plugin'.format(event.name.split('.', 1)[0].title())
                if plugin_name in self.bot.plugins:
                    self.log.info('Detected change in %s, reloading...', plugin_name)
                    try:
                        self.bot.plugins[plugin_name].reload()
                    except Exception:
                        self.log.exception('Failed to reload: ')

    def wait_for_actions(self):
        ps = rdb.pubsub()
        ps.subscribe('actions')

        for item in ps.listen():
            if item['type'] != 'message':
                continue

            data = json.loads(item['data'])
            if data['type'] == 'GUILD_UPDATE' and data['id'] in self.guilds:
                with self.send_control_message() as embed:
                    embed.title = u'Reloaded config for {}'.format(
                        self.guilds[data['id']].name
                    )

                self.log.info(u'Reloading guild %s', self.guilds[data['id']].name)

                # Refresh config, mostly to validate
                try:
                    self.guilds[data['id']].get_config(refresh=True)
                except:
                    self.log.exception(u'Failed to reload config for guild %s', self.guilds[data['id']].name)
                    return

                # Reload the guild entirely
                self.guilds[data['id']] = Guild.with_id(data['id'])
            elif data['type'] == 'RESTART':
                self.log.info('Restart requested, signaling parent')
                os.kill(os.getppid(), signal.SIGUSR1)

    def unload(self, ctx):
        ctx['guilds'] = self.guilds
        ctx['startup'] = self.startup
        super(CorePlugin, self).unload(ctx)

    def on_pre(self, plugin, func, event, args, kwargs):
        """
        This function handles dynamically dispatching and modifying events based
        on a specific guilds configuration. It is called before any handler of
        either commands or listeners.
        """
        if hasattr(event, 'guild') and event.guild:
            guild_id = event.guild.id
        elif hasattr(event, 'guild_id') and event.guild_id:
            guild_id = event.guild_id
        else:
            guild_id = None

        if guild_id not in self.guilds:
            if isinstance(event, CommandEvent):
                if event.command.metadata.get('global_', False):
                    return event
            elif hasattr(func, 'subscriptions'):
                if func.subscriptions[0].metadata.get('global_', False):
                    return event

            return

        if hasattr(plugin, 'WHITELIST_FLAG'):
            if not int(plugin.WHITELIST_FLAG) in self.guilds[guild_id].whitelist:
                return

        event.base_config = self.guilds[guild_id].get_config()

        plugin_name = plugin.name.lower().replace('plugin', '')
        if not getattr(event.base_config.plugins, plugin_name, None):
            return

        if not hasattr(event, 'config'):
            event.config = LocalProxy()

        event.config.set(getattr(event.base_config.plugins, plugin_name))
        return event

    @Plugin.schedule(290, init=False)
    def update_guild_bans(self):
        to_update = [
            guild for guild in Guild.select().where(
                (Guild.last_ban_sync < (datetime.utcnow() - timedelta(days=1))) |
                (Guild.last_ban_sync >> None)
            )
            if guild.guild_id in self.client.state.guilds]

        # Update 10 at a time
        for guild in to_update[:10]:
            guild.sync_bans(self.client.state.guilds.get(guild.guild_id))

    @Plugin.listen('GuildUpdate')
    def on_guild_udpate(self, event):
        self.log.info('Got guild update for guild %s (%s)', event.guild.id, event.guild.channels)

    @Plugin.listen('GuildMembersChunk')
    def on_guild_members_chunk(self, event):
        self.log.info('Got members chunk for guild %s', event.guild_id)

    @Plugin.listen('GuildBanAdd')
    def on_guild_ban_add(self, event):
        GuildBan.ensure(self.client.state.guilds.get(event.guild_id), event.user)

    @Plugin.listen('GuildBanRemove')
    def on_guild_ban_remove(self, event):
        GuildBan.delete().where(
            (GuildBan.user_id == event.user.id) &
            (GuildBan.guild_id == event.guild_id)
        )

    @contextlib.contextmanager
    def send_control_message(self):
        embed = MessageEmbed()
        embed.set_footer(text='Rowboat {}'.format(
            'Production' if ENV == 'prod' else 'Testing'
        ))
        embed.timestamp = datetime.utcnow().isoformat()
        embed.color = 0x779ecb
        yield embed
        self.bot.client.api.channels_messages_create(
            290924692057882635 if ENV == 'prod' else 301869081714491393,
            '',
            embed=embed
        )

    @Plugin.listen('Resumed')
    def on_resumed(self, event):
        Notification.dispatch(
            Notification.Types.RESUME,
            trace=event.trace,
            env=ENV,
        )

        with self.send_control_message() as embed:
            embed.title = 'Resumed'
            embed.color = 0xffb347
            embed.add_field(name='Gateway Server', value=event.trace[0], inline=False)
            embed.add_field(name='Session Server', value=event.trace[1], inline=False)
            embed.add_field(name='Replayed Events', value=str(self.client.gw.replayed_events))

    @Plugin.listen('Ready', priority=Priority.BEFORE)
    def on_ready(self, event):
        reconnects = self.client.gw.reconnects
        self.log.info('Started session %s', event.session_id)
        Notification.dispatch(
            Notification.Types.CONNECT,
            trace=event.trace,
            env=ENV,
        )

        with self.send_control_message() as embed:
            if reconnects:
                embed.title = 'Reconnected'
                embed.color = 0xffb347
            else:
                embed.title = 'Connected'
                embed.color = 0x77dd77

            embed.add_field(name='Gateway Server', value=event.trace[0], inline=False)
            embed.add_field(name='Session Server', value=event.trace[1], inline=False)

    @Plugin.listen('GuildCreate', conditional=lambda e: e.created is True)
    def on_guild_join(self, event):
        with self.send_control_message() as embed:
            embed.title = 'Added to Guild'
            embed.add_field(name='Guild Name', value=event.guild.name, inline=True)
            embed.add_field(name='Guild ID', value=str(event.guild.id), inline=True)

    @Plugin.listen('GuildDelete', conditional=lambda e: e.deleted is True)
    def on_guild_leave(self, event):
        with self.send_control_message() as embed:
            embed.title = 'Removed to Guild'
            embed.add_field(name='Guild ID', value=str(event.guild.id), inline=True)

    @Plugin.listen('GuildCreate', priority=Priority.BEFORE, conditional=lambda e: not e.created)
    def on_guild_create(self, event):
        try:
            guild = Guild.with_id(event.id)
        except Guild.DoesNotExist:
            return

        if not guild.enabled:
            return

        # Ensure we're updated
        self.log.info('Syncing guild %s', event.guild.id)
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

    def get_level(self, guild, user):
        config = (guild.id in self.guilds and self.guilds.get(guild.id).get_config())

        user_level = 0
        if config:
            member = guild.get_member(user)
            if not member:
                return user_level

            for oid in member.roles:
                if oid in config.levels and config.levels[oid] > user_level:
                    user_level = config.levels[oid]

            # User ID overrides should override all others
            if member.id in config.levels:
                user_level = config.levels[member.id]

        return user_level

    @Plugin.listen('MessageCreate')
    def on_message_create(self, event):
        """
        This monstrosity of a function handles the parsing and dispatching of
        commands.
        """
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
        if config and config.commands:
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

        event.user_level = self.get_level(event.guild, event.author) if event.guild else 0

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
                overrides = {}
                for obj in config.commands.get_command_override(command):
                    overrides.update(obj)

                if overrides.get('disabled'):
                    continue

                level = overrides.get('level', level)

            if not global_admin and event.user_level < level:
                continue

            with timed('rowboat.command.duration', tags={'plugin': command.plugin.name, 'command': command.name}):
                try:
                    command_event = CommandEvent(command, event.message, match)
                    command_event.user_level = event.user_level
                    command.plugin.execute(command_event)
                except CommandResponse as e:
                    event.reply(e.response)
                except:
                    Command.track(event, command, exception=True)
                    self.log.exception('Command error:')
                    return event.reply('<:{}> something went wrong, perhaps try again later'.format(RED_TICK_EMOJI))

            Command.track(event, command)

            # Dispatch the command used modlog event
            if config:
                if not hasattr(event, 'config'):
                    event.config = LocalProxy()

                modlog_config = getattr(config.plugins, 'modlog', None)
                if not modlog_config:
                    return

                event.config.set(modlog_config)
                if not event.config:
                    return

                plugin = self.bot.plugins.get('ModLogPlugin')
                if plugin:
                    plugin.log_action(Actions.COMMAND_USED, event)

            return

    @Plugin.command('setup')
    def command_setup(self, event):
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

        guild = Guild.setup(event.guild)
        self.guilds[event.guild.id] = guild
        event.msg.reply(':ok_hand: successfully loaded configuration')

    @Plugin.command('nuke', '<user:snowflake> <reason:str...>', level=-1)
    def nuke(self, event, user, reason):
        contents = []

        for gid, guild in self.guilds.items():
            guild = self.state.guilds[gid]
            perms = guild.get_permissions(self.state.me)

            if not perms.ban_members and not perms.administrator:
                contents.append(u':x: {} (`{}`) - No Permissions'.format(
                    guild.name,
                    gid
                ))
                continue

            try:
                Infraction.ban(
                    self,
                    event,
                    user,
                    reason,
                    guild=guild)
            except:
                contents.append(u':x: {} (`{}`) - Unknown Error'.format(
                    guild.name,
                    gid
                ))
                self.log.exception('Failed to force ban %s in %s', user, gid)

            contents.append(u':white_check_mark: {} (`{}`) - :regional_indicator_f:'.format(
                guild.name,
                gid
            ))

        event.msg.reply('Results:\n' + '\n'.join(contents))

    @Plugin.command('about')
    def command_about(self, event):
        embed = MessageEmbed()
        embed.set_author(name='Rowboat', icon_url=self.client.state.me.avatar_url, url='https://docs.rowboat.party/')
        embed.description = BOT_INFO
        embed.add_field(name='Servers', value=str(Guild.select().count()), inline=True)
        embed.add_field(name='Uptime', value=humanize.naturaltime(datetime.utcnow() - self.startup), inline=True)
        event.msg.reply('', embed=embed)

    @Plugin.command('uptime', level=-1)
    def command_uptime(self, event):
        event.msg.reply('Rowboat was started {}'.format(
            humanize.naturaltime(datetime.utcnow() - self.startup)
        ))

    @Plugin.command('source', '<command>', level=-1)
    def command_source(self, event, command=None):
        for cmd in self.bot.commands:
            if command.lower() in cmd.triggers:
                break
        else:
            event.msg.reply(u"Couldn't find command for `{}`".format(S(command, escape_codeblocks=True)))
            return

        code = cmd.func.__code__
        lines, firstlineno = inspect.getsourcelines(code)

        event.msg.reply('<https://github.com/b1naryth1ef/rowboat/blob/master/{}#L{}-{}>'.format(
            code.co_filename,
            firstlineno,
            firstlineno + len(lines)
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

            result = pprint.pformat(local['x'])
        else:
            try:
                result = str(eval(src, ctx))
            except Exception as e:
                event.msg.reply(PY_CODE_BLOCK.format(type(e).__name__ + ': ' + str(e)))
                return

        if len(result) > 1990:
            event.msg.reply('', attachments=[('result.txt', result)])
        else:
            event.msg.reply(PY_CODE_BLOCK.format(result))

    @Plugin.command('sync-bans', group='control', level=-1)
    def control_sync_bans(self, event):
        guilds = list(Guild.select().where(
            Guild.enabled == 1
        ))

        msg = event.msg.reply(':timer: pls wait while I sync...')

        for guild in guilds:
            guild.sync_bans(self.client.state.guilds.get(guild.guild_id))

        msg.edit('<:{}> synced {} guilds'.format(GREEN_TICK_EMOJI, len(guilds)))

    @Plugin.command('reconnect', group='control', level=-1)
    def control_reconnect(self, event):
        event.msg.reply('Ok, closing connection')
        self.client.gw.ws.close()
