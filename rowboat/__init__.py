import os

from raven import Client
VERSION = '0.0.1'


raven_client = Client(
    'https://d4e453a9eaf940ca9ddcc73c139120de:f173949164834738aaac632708dac695@sentry.io/119318',
    ignore_exceptions=[
        'KeyboardInterrupt',
    ],
    release=VERSION,
    environment=os.getenv('ENV', 'local')
)
