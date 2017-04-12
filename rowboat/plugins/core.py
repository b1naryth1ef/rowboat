import os
import json
import pprint
import signal
import humanize
import functools
import inspect

from datetime import datetime, timedelta
from holster.emitter import Priority
from disco.types.message import MessageEmbed
from disco.api.http import APIException
from disco.bot.command import CommandEvent

from rowboat.util import C, LocalProxy
from rowboat.plugins import BasePlugin as Plugin
from rowboat.plugins import RowboatPlugin
from rowboat.sql import init_db
from rowboat.redis import rdb
from rowboat.models.user import Infraction
from rowboat.models.guild import Guild, GuildBan
from rowboat.models.notification import Notification
from rowboat.plugins.modlog import Actions

ENV = os.getenv('ENV', 'local')

PY_CODE_BLOCK = u'```py\n{}\n```'

BOT_INFO = '''
Rowboat is a moderation and utilitarian Bot built for large Discord servers.
'''


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

        self.spawn(self.wait_for_actions)
        self.spawn(self.wait_for_dispatches)

    def wait_for_dispatches(self):
        ps = rdb.pubsub()
        ps.subscribe('notifications')

        for item in ps.listen():
            if item['type'] != 'message':
                continue

            obj = json.loads(item['data'])

            self.bot.client.api.channels_messages_create(
                290924692057882635,
                u'**{}**\n{}'.format(
                    obj['title'],
                    obj['content']))

    def wait_for_actions(self):
        ps = rdb.pubsub()
        ps.subscribe('actions')

        for item in ps.listen():
            if item['type'] != 'message':
                continue

            data = json.loads(item['data'])
            if data['type'] == 'GUILD_UPDATE' and data['id'] in self.guilds:
                self.send_control_message(u'Reloaded config for Guild {}'.format(self.guilds[data['id']].name))
                self.log.info('Reloading config for guild %s', self.guilds[data['id']].name)
                self.guilds[data['id']].get_config(refresh=True)
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
            return

        if guild_id not in self.guilds:
            if isinstance(event, CommandEvent):
                if event.command.metadata.get('global_', False):
                    return event
            elif hasattr(func, 'subscriptions'):
                if func.subscriptions[0].metadata.get('global_', False):
                    return event

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

    def send_control_message(self, content, *args, **kwargs):
        self.bot.client.api.channels_messages_create(
            290924692057882635,
            u'**{}**\n{}'.format('Production' if ENV == 'prod' else 'Local', content), *args, **kwargs)

    @Plugin.listen('Resumed')
    def on_resumed(self, event):
        Notification.dispatch(
            Notification.Types.RESUME,
            trace=event.trace,
            env=ENV,
        )

    @Plugin.listen('Ready')
    def on_ready(self, event):
        self.log.info('Started session %s', event.session_id)
        Notification.dispatch(
            Notification.Types.CONNECT,
            trace=event.trace,
            env=ENV,
        )

    @Plugin.schedule(1, init=False)
    def on_fucked_channels(self):
        for guild in self.state.guilds.values():
            if not len(guild.channels):
                self.log.warning('Guild %s (%s) has fucked channels', guild.id, guild.name)

    # @Plugin.listen('GuildCreate', conditional=lambda e: e.created is True)
    # def on_guild_join(self, event):
    #     self.send_control_message('Added to guild {}: {}'.format(event.guild.id, event.guild.name))

    @Plugin.listen('GuildDelete', conditional=lambda e: e.deleted is True)
    def on_guild_leave(self, event):
        self.send_control_message('Removed from guild {}'.format(event.guild.id))

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
            member = guild.get_member(user.id)
            if not member:
                return user_level

            for oid in member.roles:
                if oid in config.levels and config.levels[oid] > user_level:
                    user_level = config.levels[oid]

            # User ID overrides should override all others
            if user.id in config.levels:
                user_level = config.levels[user.id]

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

    @Plugin.command('setup')
    def command_setup(self, event):
        """
        Setup a new Guild with Rowboat
        """
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

    '''
    @Plugin.command('help', '<command>')
    def command_help(self, event, command):
        """
        Provides information on a given command.
        """
        for cmd in self.bot.commands:
            if command.lower() in cmd.triggers:
                break
        else:
            event.msg.reply(u"Couldn't find command for `{}`".format(C(command)))
            return

        event.msg.reply(u'Usage: {} {}\nDescription: {}'.format(
            command,
            cmd.raw_args or '',
            cmd.func.__doc__,
        ))
    '''

    @Plugin.command('nuke', '<user:snowflake> <reason:str...>', level=-1)
    def nuke(self, event, user, reason):
        """
        Force ban a user from all rowboat Guilds
        """
        for gid, guild in self.guilds.items():
            try:
                Infraction.ban(
                    self,
                    event,
                    user,
                    reason,
                    guild=self.state.guilds[gid])
            except:
                self.log.exception('Failed to force ban %s in %s', user, gid)

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
            event.msg.reply(u"Couldn't find command for `{}`".format(C(command)))
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
            event.msg.reply('', attachment=('result.txt', result))
        else:
            event.msg.reply(PY_CODE_BLOCK.format(result))
