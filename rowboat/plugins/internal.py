import gevent
from gevent.lock import Semaphore
from datetime import datetime, timedelta


from disco.gateway.packets import OPCode, RECV
from rowboat.plugins import BasePlugin as Plugin
from rowboat.models.event import Event


class EventLogPlugin(Plugin):
    def load(self, ctx):
        super(EventLogPlugin, self).load(ctx)

        self.session_id = None
        self.lock = Semaphore()
        self.cache = []

    @Plugin.schedule(300, init=False)
    def prune_old_events(self):
        # Keep 12 hours of PRESENCE/TYPING events
        Event.delete().where(
            (Event.event << ('PRESENCE_UPDATE', 'TYPING_START')) &
            (Event.timestamp > datetime.utcnow() - timedelta(hours=12))
        ).execute()

        # And 3 days of everything else
        Event.delete().where(
            (Event.timestamp > datetime.utcnow() - timedelta(days=3))
        ).execute()

    @Plugin.listen('Ready')
    def on_ready(self, event):
        self.session_id = event.session_id
        gevent.spawn(self.flush_cache)

    @Plugin.listen_packet((RECV, OPCode.DISPATCH))
    def on_gateway_event(self, event):
        with self.lock:
            self.cache.append(event)

    def flush_cache(self):
        while True:
            gevent.sleep(1)

            if not len(self.cache):
                continue

            with self.lock:
                Event.insert_many([
                    Event.prepare(self.session_id, event) for event in self.cache
                ]).execute()
                self.cache = []
