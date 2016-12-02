from disco.bot import Plugin
from raven import Client

VERSION = '0.0.1'


client = Client(
    'https://d4e453a9eaf940ca9ddcc73c139120de:f173949164834738aaac632708dac695@sentry.io/119318',
    ignore_exceptions=[
        'KeyboardInterrupt',
    ]
)


class RowboatPlugin(Plugin):
    _shallow = True
    whitelisted = False
