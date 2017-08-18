import gevent
from gevent.lock import Semaphore
from datetime import datetime, timedelta

from peewee import fn
from disco.gateway.packets import OPCode, RECV
from disco.types.message import MessageTable, MessageEmbed

from rowboat.redis import rdb
from rowboat.plugins import BasePlugin as Plugin
from rowboat.util.redis import RedisSet
from rowboat.models.event import Event
from rowboat.models.user import User
from rowboat.models.channel import Channel
from rowboat.models.message import Command, Message


class InternalPlugin(Plugin):
    global_plugin = True

    def load(self, ctx):
        super(InternalPlugin, self).load(ctx)

        self.events = RedisSet(rdb, 'internal:tracked-events')
        self.session_id = None
        self.lock = Semaphore()
        self.cache = []

    @Plugin.command('errors', group='commands', level=-1)
    def on_commands_errors(self, event):
        q = Command.select().join(
            Message, on=(Command.message_id == Message.id)
        ).where(
            Command.success == 0
        ).order_by(Message.timestamp.desc()).limit(10)

        tbl = MessageTable()
        tbl.set_header('ID', 'Command', 'Error')

        for err in q:
            tbl.add(err.message_id, u'{}.{}'.format(err.plugin, err.command), err.traceback.split('\n')[-2])

        event.msg.reply(tbl.compile())

    @Plugin.command('info', '<mid:snowflake>', group='commands', level=-1)
    def on_commands_info(self, event, mid):
        cmd = Command.select(Command, Message, Channel).join(
            Message, on=(Command.message_id == Message.id).alias('message')
        ).join(
            Channel, on=(Channel.channel_id == Message.channel_id).alias('channel')
        ).join(
            User, on=(User.user_id == Message.author_id).alias('author')
        ).where(
            Command.message_id == mid
        ).order_by(
            Message.timestamp.desc(),
        ).get()

        embed = MessageEmbed()
        embed.title = '{}.{} ({})'.format(cmd.plugin, cmd.command, cmd.message.id)
        embed.set_author(name=unicode(cmd.message.author), icon_url=cmd.message.author.get_avatar_url())
        embed.color = 0x77dd77 if cmd.success else 0xff6961

        if not cmd.success:
            embed.description = u'```{}```'.format(cmd.traceback)

        embed.add_field(name='Message', value=cmd.message.content)
        embed.add_field(name='Channel', value=u'{} `{}`'.format(cmd.message.channel.name, cmd.message.channel.channel_id))
        embed.add_field(name='Guild', value=unicode(cmd.message.guild_id))
        event.msg.reply(embed=embed)

    @Plugin.command('usage', group='commands', level=-1)
    def on_commands_usage(self, event):
        q = Command.select(
            fn.COUNT('*'),
            Command.plugin,
            Command.command,
        ).group_by(
            Command.plugin, Command.command
        ).order_by(fn.COUNT('*').desc()).limit(25)

        tbl = MessageTable()
        tbl.set_header('Plugin', 'Command', 'Usage')

        for count, plugin, command in q.tuples():
            tbl.add(plugin, command, count)

        event.msg.reply(tbl.compile())

    @Plugin.command('stats', '<name:str>', group='commands', level=-1)
    def on_commands_stats(self, event, name):
        if '.' in name:
            plugin, command = name.split('.', 1)
            q = (
                (Command.plugin == plugin) &
                (Command.command == command)
            )
        else:
            q = (Command.command == name)

        result = list(Command.select(
            fn.COUNT('*'),
            Command.success,
        ).where(q).group_by(Command.success).order_by(fn.COUNT('*').desc()).tuples())

        success, error = 0, 0
        for count, check in result:
            if check:
                success = count
            else:
                error = count

        event.msg.reply('Command `{}` was used a total of {} times, {} of those had errors'.format(
            name,
            success + error,
            error
        ))

    @Plugin.command('throw', level=-1)
    def on_throw(self, event):
        raise Exception('Internal.throw')

    @Plugin.command('add', '<name:str>', group='events', level=-1)
    def on_events_add(self, event, name):
        self.events.add(name)
        event.msg.reply(':ok_hand: added {} to the list of tracked events'.format(name))

    @Plugin.command('remove', '<name:str>', group='events', level=-1)
    def on_events_remove(self, event, name):
        self.events.remove(name)
        event.msg.reply(':ok_hand: removed {} from the list of tracked events'.format(name))

    @Plugin.schedule(300, init=False)
    def prune_old_events(self):
        # Keep 24 hours of all events
        Event.delete().where(
            (Event.timestamp > datetime.utcnow() - timedelta(hours=24))
        ).execute()

    @Plugin.listen('Ready')
    def on_ready(self, event):
        self.session_id = event.session_id
        gevent.spawn(self.flush_cache)

    @Plugin.listen_packet((RECV, OPCode.DISPATCH))
    def on_gateway_event(self, event):
        if event['t'] not in self.events:
            return

        with self.lock:
            self.cache.append(event)

    def flush_cache(self):
        while True:
            gevent.sleep(1)

            if not len(self.cache):
                continue

            with self.lock:
                Event.insert_many(filter(bool, [
                    Event.prepare(self.session_id, event) for event in self.cache
                ])).execute()
                self.cache = []
