from __future__ import absolute_import

import gevent


def wait_many(*args, **kwargs):
    def _async():
        for awaitable in args:
            awaitable.wait()

    gevent.spawn(_async).get(timeout=kwargs.get('timeout', None))
