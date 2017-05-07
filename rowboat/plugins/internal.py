import gevent
from gevent.lock import Semaphore
from datetime import datetime, timedelta

from disco.gateway.packets import OPCode, RECV

from rowboat.redis import rdb
from rowboat.plugins import BasePlugin as Plugin
from rowboat.util.redis import RedisSet
from rowboat.models.event import Event


class EventLogPlugin(Plugin):
    global_plugin = True

    def load(self, ctx):
        super(EventLogPlugin, self).load(ctx)

        self.events = RedisSet(rdb, 'internal:tracked-events')
        self.session_id = None
        self.lock = Semaphore()
        self.cache = []

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
