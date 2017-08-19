import time

import gevent
from disco.api.http import APIException
from disco.util.logging import LoggingClass


class ModLogPump(LoggingClass):
    def __init__(self, channel, sleep_duration=5):
        self.channel = channel
        self.sleep_duration = sleep_duration
        self._buffer = []
        self._have = gevent.event.Event()
        self._quiescent_period = None
        self._lock = gevent.lock.Semaphore()

        self._greenlet = gevent.spawn(self._emitter_loop)

    def _start_emitter(self, greenlet=None):
        self.log.warning('Restarting emitter for ModLogPump %s' % self.channel)
        self._greenlet = gevent.spawn(self._emitter_loop)
        self._greenlet.link_exception(self._start_emitter)

    def __del__(self):
        if self._greenlet:
            self._greenlet.kill()

    def _emitter_loop(self):
        while True:
            self._have.wait()

            backoff = False

            with self.channel.client.api.capture() as responses:
                try:
                    self._emit()
                except APIException as e:
                    # Message send is disabled
                    if e.code == 40004:
                        backoff = True
                except Exception:
                    self.log.exception('Exception when executing ModLogPump._emit: ')

            if responses.rate_limited:
                backoff = True

            # If we need to backoff, set a quiescent period that will batch
            #  requests for the next 60 seconds.
            if backoff:
                self._quiescent_period = time.time() + 60

            if self._quiescent_period:
                if self._quiescent_period < time.time():
                    self._quiescent_period = None
                else:
                    gevent.sleep(self.sleep_duration)

            if not self._buffer:
                self._have.clear()

    def _emit(self):
        with self._lock:
            msg = self._get_next_message()
            if not msg:
                return

            self.channel.send_message(msg)

    def _get_next_message(self):
        data = ''

        while self._buffer:
            payload = self._buffer.pop(0)
            if len(data) + (len(payload) + 1) > 2000:
                break

            data += '\n'
            data += payload

        return data

    def send(self, payload):
        with self._lock:
            self._buffer.append(payload)
            self._have.set()
