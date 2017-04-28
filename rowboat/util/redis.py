import gevent

from gevent.lock import Semaphore


class RedisSet(object):
    def __init__(self, rdb, key_name):
        self.rdb = rdb
        self.key_name = key_name
        self.update_key_name = u'redis-set:{}'.format(self.key_name)

        self._set = rdb.smembers(key_name)
        self._lock = Semaphore()
        self._ps = self.rdb.pubsub()
        self._ps.subscribe(self.update_key_name)

        self._inst = gevent.spawn(self._listener)

    def __contains__(self, other):
        return other in self._set

    def add(self, key):
        if key in self._set:
            return

        with self._lock:
            self.rdb.sadd(self.key_name, key)
            self._set.add(key)
            self.rdb.publish(self.update_key_name, u'A{}'.format(key))

    def remove(self, key):
        if key not in self._set:
            return

        with self._lock:
            self.rdb.srem(self.key_name, key)
            self._set.remove(key)
            self.rdb.publish(self.update_key_name, u'R{}'.format(key))

    def _listener(self):
        for item in self._ps.listen():
            if item['type'] != 'message':
                continue

            with self._lock:
                op, data = item['data'][0], item['data'][1:]

                if op == 'A':
                    if data not in self._set:
                        self._set.add(data)
                elif op == 'R':
                    if data in self._set:
                        self._set.remove(data)
