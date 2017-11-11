from gevent.monkey import patch_all
patch_all()  # noqa: E402

import time

from gevent.event import AsyncResult, Event

from datetime import datetime, timedelta
from rowboat.util.timing import Eventual


def test_eventual_accuracy():
    result = AsyncResult()
    should_be_called_at = None

    def f():
        result.set(datetime.utcnow())

    e = Eventual(f)
    should_be_called_at = datetime.utcnow() + timedelta(milliseconds=100)
    e.set_next_schedule(should_be_called_at)
    called_at = result.get()

    assert called_at >= should_be_called_at


def test_eventual_lowering():
    result = AsyncResult()

    e = Eventual(lambda: result.set(datetime.utcnow()))
    e.set_next_schedule(
        datetime.utcnow() + timedelta(seconds=10)
    )
    e.set_next_schedule(
        datetime.utcnow() + timedelta(seconds=5)
    )

    should_be_called_at = datetime.utcnow() + timedelta(milliseconds=100)
    e.set_next_schedule(should_be_called_at)

    called_at = result.get()
    assert called_at >= should_be_called_at
    assert called_at <= should_be_called_at + timedelta(milliseconds=100)


def test_eventual_coalesce():
    ref = {'value': 0}
    done = Event()
    e = Eventual(None)

    def func():
        ref['value'] += 1

        # Only the first time
        if ref['value'] == 1:
            e.set_next_schedule(datetime.utcnow() - timedelta(seconds=10))
            time.sleep(1)
            done.set()

    e.function = func
    e.set_next_schedule(datetime.utcnow() + timedelta(milliseconds=500))
    done.wait()
    assert ref['value'] == 1
