import os
import subprocess

from raven import Client
from raven.transport.gevent import GeventedHTTPTransport

ENV = os.getenv('ENV', 'local')
DSN = os.getenv('DSN')
REV = subprocess.check_output(['git', 'rev-parse', 'HEAD']).strip()

raven_client = Client(
    DSN,
    ignore_exceptions=[
        'KeyboardInterrupt',
    ],
    release=REV,
    environment=ENV,
    transport=GeventedHTTPTransport,
)
