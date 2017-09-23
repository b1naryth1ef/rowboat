import json
import uuid
import logging
import time
import os
import gevent

from gevent.lock import Semaphore
from rowboat.redis import rdb

log = logging.getLogger(__name__)

TASKS = {}


def get_client():
    from disco.client import ClientConfig, Client
    from rowboat.config import token

    config = ClientConfig()
    config.token = token
    return Client(config)


def task(*args, **kwargs):
    """
    Register a new task.
    """
    def deco(f):
        task = Task(f.__name__, f, *args, **kwargs)

        if f.__name__ in TASKS:
            raise Exception("Conflicting task name: %s" % f.__name__)

        TASKS[f.__name__] = task
        return task
    return deco


class Task(object):
    def __init__(self, name, method, max_concurrent=None, buffer_time=None, max_queue_size=25):
        self.name = name
        self.method = method
        self.max_concurrent = max_concurrent
        self.max_queue_size = max_queue_size
        self.buffer_time = buffer_time

        self.log = log

    def __call__(self, *args, **kwargs):
        return self.method(self, *args, **kwargs)

    def queue(self, *args, **kwargs):
        # Make sure we have space
        if self.max_queue_size and (rdb.llen('task_queue:%s' % self.name) or 0) > self.max_queue_size:
            raise Exception("Queue for task %s is full!" % self.name)

        task_id = str(uuid.uuid4())
        rdb.rpush('task_queue:%s' % self.name, json.dumps({
            'id': task_id,
            'args': args,
            'kwargs': kwargs
        }))
        return task_id


class TaskRunner(object):
    def __init__(self, name, task):
        self.name = name
        self.task = task
        self.lock = Semaphore(task.max_concurrent)

    def process(self, job):
        log.info('[%s] Running job %s...', job['id'], self.name)
        start = time.time()

        try:
            self.task(*job['args'], **job['kwargs'])
            if self.task.buffer_time:
                time.sleep(self.task.buffer_time)
        except:
            log.exception('[%s] Failed in %ss', job['id'], time.time() - start)

        log.info('[%s] Completed in %ss', job['id'], time.time() - start)

    def run(self, job):
        if self.task.max_concurrent:
            self.lock.acquire()

        self.process(job)

        if self.task.max_concurrent:
            self.lock.release()


class TaskWorker(object):
    def __init__(self):
        self.load()
        self.queues = ['task_queue:' + i for i in TASKS.keys()]
        self.runners = {k: TaskRunner(k, v) for k, v in TASKS.items()}
        self.active = True

    def load(self):
        for f in os.listdir(os.path.dirname(os.path.abspath(__file__))):
            if f.endswith('.py') and not f.startswith('__'):
                __import__('rowboat.tasks.' + f.rsplit('.')[0])

    def run(self):
        log.info('Running TaskManager on %s queues...', len(self.queues))

        while self.active:
            chan, job = rdb.blpop(self.queues)
            job_name = chan.split(':', 1)[1]
            job = json.loads(job)

            if job_name not in TASKS:
                log.error("Cannot handle task %s", job_name)
                continue

            gevent.spawn(self.runners[job_name].run, job)
