import os

from raven import Client
from raven.transport.gevent import GeventedHTTPTransport

VERSION = '0.0.1'

ENV = os.getenv('ENV', 'local')
DSN = os.getenv('DSN')

raven_client = Client(
    DSN,
    ignore_exceptions=[
        'KeyboardInterrupt',
    ],
    release=VERSION,
    environment=ENV,
    transport=GeventedHTTPTransport,
)
