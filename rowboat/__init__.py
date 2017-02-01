import os

from raven import Client
from disco.bot import Plugin

from disco.api.http import APIException
from disco.bot.command import CommandEvent
from disco.gateway.events import GatewayEvent

VERSION = '0.0.1'


raven_client = Client(
    'https://d4e453a9eaf940ca9ddcc73c139120de:f173949164834738aaac632708dac695@sentry.io/119318',
    ignore_exceptions=[
        'KeyboardInterrupt',
    ],
    release=VERSION,
    environment=os.getenv('ENV', 'local')
)


class RavenPlugin(object):
    def handle_exception(self, greenlet, event):
        extra = {}

        if isinstance(greenlet.exception, APIException):
            extra['status_code'] = greenlet.exception.response.status_code
            extra['code'] = greenlet.exception.code
            extra['msg'] = greenlet.exception.msg
            extra['content'] = greenlet.exception.response.content

        if isinstance(event, CommandEvent):
            extra['command'] = {
                'name': event.name,
                'plugin': event.command.plugin.__class__.__name__,
                'content': event.msg.content,
            }
            extra['author'] = event.msg.author.to_dict(),
            extra['channel'] = {
                'id': event.channel.id,
                'name': event.channel.name,
            }

            if event.guild:
                extra['guild'] = {
                    'id': event.guild.id,
                    'name': event.guild.name,
                }
        elif isinstance(event, GatewayEvent):
            extra['event'] = {
                'name': event.__class__.__name__,
                'data': event.to_dict(),
            }

        raven_client.captureException(exc_info=greenlet.exc_info, extra=extra)


class BasePlugin(RavenPlugin, Plugin):
    _shallow = True


class RowboatPlugin(RavenPlugin, Plugin):
    _shallow = True
    whitelisted = False
