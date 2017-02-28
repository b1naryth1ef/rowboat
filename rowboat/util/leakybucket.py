import time


def get_ms_time():
    return int(time.time() * 1000)

# function(keys=[rl_key], args=[time.time() - (time_period * max_actions), time.time()]
INCR_SCRIPT = '''
local key = KEYS[1]

-- Clear out expired water drops
redis.call("ZREMRANGEBYSCORE", KEYS[1], "-inf", ARGV[2])

-- Add our keys
for i=1,ARGV[1] do
  redis.call("ZADD", KEYS[1], ARGV[3], ARGV[3] + i)
end

redis.call("EXPIRE", KEYS[1], ARGV[4])

return redis.call("ZCOUNT", KEYS[1], "-inf", "+inf")
'''

GET_SCRIPT = '''
local key = KEYS[1]

-- Clear out expired water drops
redis.call("ZREMRANGEBYSCORE", KEYS[1], "-inf", ARGV[1])

return redis.call("ZCOUNT", KEYS[1], "-inf", "+inf")
'''


class LeakyBucket(object):
    def __init__(self, redis, key_fmt, max_actions, time_period):
        self.redis = redis
        self.key_fmt = key_fmt
        self.max_actions = max_actions
        self.time_period = time_period

        self._incr_script = self.redis.register_script(INCR_SCRIPT)
        self._get_script = self.redis.register_script(GET_SCRIPT)

    def incr(self, key, amount=1):
        key = self.key_fmt.format(key)
        return int(self._incr_script(
            keys=[key],
            args=[
                amount,
                get_ms_time() - self.time_period,
                get_ms_time(),
                (get_ms_time() + (self.time_period * 2)) / 1000,
            ]))

    def check(self, key, amount=1):
        count = self.incr(key, amount)
        if count >= self.max_actions:
            return False
        return True

    def get(self, key):
        return int(self._get_script(self.key_fmt.format(key)))

    def clear(self, key):
        self.redis.zremrangebyscore(self.key_fmt.format(key), '-inf', '+inf')

    def count(self, key):
        return self.redis.zcount(self.key_fmt.format(key), '-inf', '+inf')

    def size(self, key):
        res = map(int, self.redis.zrangebyscore(self.key_fmt.format(key), '-inf', '+inf'))
        if len(res) <= 1:
            return 0
        return (res[-1] - res[0]) / 1000.0
