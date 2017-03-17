import time
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


class Debounce(object):
    def __init__(self, func, default, hardlimit, **kwargs):
        self.func = func
        self.default = default
        self.hardlimit = hardlimit
        self.kwargs = kwargs

        self._start = time.time()
        self._lock = Semaphore()
        self._t = gevent.spawn(self.wait)

    def wait(self):
        gevent.sleep(self.default)

        with self._lock:
            self.func(**self.kwargs)
            self._t = None

    def touch(self):
        if self._t:
            with self._lock:
                self._t.kill()
                self._t = None
        else:
            self._start = time.time()

        if time.time() - self._start > self.hardlimit:
            gevent.spawn(self.func, **self.kwargs)
            return

        self._t = gevent.spawn(self.wait)
