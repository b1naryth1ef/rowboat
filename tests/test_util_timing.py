import unittest

from gevent.event import AsyncResult

from datetime import datetime, timedelta
from rowboat.util.timing import Eventual


class TestEventual(unittest.TestCase):
    def test_eventual_accuracy(self):
        result = AsyncResult()
        should_be_called_at = None

        def f():
            result.set(datetime.utcnow())

        e = Eventual(f)
        should_be_called_at = datetime.utcnow() + timedelta(milliseconds=100)
        e.set_next_schedule(should_be_called_at)
        called_at = result.get()
        self.assertGreater(called_at, should_be_called_at)
