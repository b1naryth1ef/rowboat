import time

from contextlib import contextmanager
from datadog import statsd


@contextmanager
def timed(metricname, tags=None):
    start = time.time()
    try:
        yield
    except:
        raise
    finally:
        statsd.timing(metricname, (time.time() - start) * 1000, tags=['{}:{}'.format(k, v) for k, v in (tags or {}).items()])
