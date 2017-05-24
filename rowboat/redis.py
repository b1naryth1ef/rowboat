from __future__ import absolute_import

import os
import redis

ENV = os.getenv('ENV', 'local')

if ENV == 'docker':
    rdb = redis.Redis(db=0, host='redis')
else:
    rdb = redis.Redis(db=11)
