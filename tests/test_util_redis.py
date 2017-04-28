import time
import unittest

from gevent import monkey; monkey.patch_all()

from rowboat.redis import rdb
from rowboat.util.redis import RedisSet


class TestRedisSet(unittest.TestCase):
    def test_basic_set(self):
        rdb.delete('TESTING:test-set')
        s1 = RedisSet(rdb, 'TESTING:test-set')
        s2 = RedisSet(rdb, 'TESTING:test-set')

        s1.add('1')
        s2.add('2')
        s1.add('3')
        s2.add('4')
        s1.add('4')
        s2.remove('4')
        s1.remove('3')
        s1.add('6')

        time.sleep(1)

        self.assertEquals(s1._set, s2._set)
