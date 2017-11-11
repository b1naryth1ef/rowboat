from __future__ import absolute_import

import time
import gevent

from gevent.lock import Semaphore
from datetime import datetime


class Eventual(object):
    """
    A class which wraps a function which will be called at somepoint in the future.
    """
    def __init__(self, function):
        self.function = function

        self._next_execution_time = None
        self._waiter_greenlet = None
        self._mutex = Semaphore()

    def _execute(self):
        """
        Executes the Eventual function, guarded by a lock.
        """
        with self._mutex:
            if self._waiter_greenlet:
                self._waiter_greenlet.kill()
                self._waiter_greenlet = None

            self.function()
            self._next_execution_time = None

    def _waiter(self):
        # Calculate the time we have to wait before next_execute_time
        wait_duration = (self._next_execution_time - datetime.utcnow())

        # Now start sleeping, we may not wake up if someone interrupts us with
        #  a more recent next_execution_time
        gevent.sleep(
            (wait_duration.seconds) + (wait_duration.microseconds / 1000000.0)
        )

        # Finally execute the function, spawn this so when we kill our waiter
        #  within _execute we don't die
        gevent.spawn(self._execute)

    def set_next_schedule(self, date):
        # If the date has already passed, kill our waiter and execute the function
        if date < datetime.utcnow():
            gevent.spawn(self._execute)
            return

        # Otherwise if we aren't waiting yet, OR if the time is newer than
        #  our current next_execute_time we need to kill the waiter and spawn
        #  a new one
        if not self._next_execution_time or date < self._next_execution_time:
            with self._mutex:
                if self._waiter_greenlet:
                    self._waiter_greenlet.kill()
                self._next_execution_time = date
                self._waiter_greenlet = gevent.spawn(self._waiter)


class Debounce(object):
    def __init__(self, func, default, hardlimit, **kwargs):
        self.func = func
        self.default = default
        self.hardlimit = hardlimit
        self.kwargs = kwargs

        self._start = time.time()
        self._lock = Semaphore()
        self._t = gevent.spawn(self.wait)

    def active(self):
        return self._t is not None

    def wait(self):
        gevent.sleep(self.default)

        with self._lock:
            self.func(**self.kwargs)
            self._t = None

    def touch(self):
        if self._t:
            with self._lock:
                if self._t:
                    self._t.kill()
                    self._t = None
        else:
            self._start = time.time()

        if time.time() - self._start > self.hardlimit:
            gevent.spawn(self.func, **self.kwargs)
            return

        self._t = gevent.spawn(self.wait)
