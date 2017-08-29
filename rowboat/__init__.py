import os
import logging
import subprocess

from disco.util.logging import LOG_FORMAT
from raven import Client
from raven.transport.gevent import GeventedHTTPTransport

ENV = os.getenv('ENV', 'local')
DSN = os.getenv('DSN')
REV = subprocess.check_output(['git', 'rev-parse', 'HEAD']).strip()

VERSION = '1.1.1'

raven_client = Client(
    DSN,
    ignore_exceptions=[
        'KeyboardInterrupt',
    ],
    release=REV,
    environment=ENV,
    transport=GeventedHTTPTransport,
)

# Log things to file
file_handler = logging.FileHandler('rowboat.log')
log = logging.getLogger()
file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
log.addHandler(file_handler)
