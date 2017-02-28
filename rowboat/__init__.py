import os

from raven import Client
VERSION = '0.0.1'


raven_client = Client(
    'https://7814a66f7ec44069be50f954f969e6e6:0e51337d3c1442a0b3521422b4db3bca@sentry.hydr0.com/2',
    ignore_exceptions=[
        'KeyboardInterrupt',
    ],
    release=VERSION,
    environment=os.getenv('ENV', 'local')
)
