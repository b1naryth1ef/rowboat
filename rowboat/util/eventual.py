import gevent

from gevent.lock import Semaphore
from datetime import datetime


class Eventual(object):
    """
    A function that will be triggered eventually.
    """

    def __init__(self, func):
        self.func = func
        self.lock = Semaphore()

        self._next = None
        self._t = None

    def wait(self, nxt):
        def f():
            gevent.sleep((self._next - datetime.utcnow()).seconds)

            with self.lock:
                self._t = None
                self.func()

        with self.lock:
            if self._t:
                self._t.kill()
                self._t = None

            self._next = nxt
            gevent.spawn(f)

    def trigger(self):
        with self.lock:
            if self._t:
                self._t.kill()
                self._t = None
                self._next = None
            self.func()

    def set_next_schedule(self, date):
        if date < datetime.utcnow():
            return gevent.spawn(self.trigger)

        if not self._next or date < self._next:
            self.wait(date)
