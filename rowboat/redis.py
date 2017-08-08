from __future__ import absolute_import

import os
import json

import redis

ENV = os.getenv('ENV', 'local')

if ENV == 'docker':
    rdb = redis.Redis(db=0, host='redis')
else:
    rdb = redis.Redis(db=11)


def emit(typ, **kwargs):
    kwargs['type'] = typ
    rdb.publish('actions', json.dumps(kwargs))
