from __future__ import absolute_import

import gevent


def wait_many(*args, **kwargs):
    def _async():
        for awaitable in args:
            awaitable.wait()

    gevent.spawn(_async).get(timeout=kwargs.get('timeout', None))

    if kwargs.get('track_exceptions', True):
        from rowboat import raven_client
        for awaitable in args:
            if awaitable.exception:
                raven_client.captureException(exc_info=awaitable.exc_info)
