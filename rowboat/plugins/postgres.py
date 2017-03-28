import gevent
from gevent.lock import Semaphore

from disco.gateway.packets import OPCode, RECV
from rowboat.plugins import BasePlugin as Plugin
from rowboat.models.event import Event

IGNORED_EVENTS = (
    'GUILD_MEMBERS_CHUNK',
    'PRESENCE_UPDATE',
    'TYPING_START'
)


class PostgresPlugin(Plugin):
    def load(self, ctx):
        super(PostgresPlugin, self).load(ctx)

        self.session_id = None
        self.lock = Semaphore()
        self.cache = []

    @Plugin.listen('Ready')
    def on_ready(self, event):
        self.session_id = event.session_id
        gevent.spawn(self.flush_cache)

    @Plugin.listen_packet((RECV, OPCode.DISPATCH))
    def on_gateway_event(self, event):
        if event['t'] in IGNORED_EVENTS:
            return

        with self.lock:
            self.cache.append(event)

    def flush_cache(self):
        while True:
            gevent.sleep(1)

            if not len(self.cache):
                return

            with self.lock:
                Event.insert_many([
                    Event.prepare(self.session_id, event) for event in self.cache
                ]).execute()
                self.cache = []
