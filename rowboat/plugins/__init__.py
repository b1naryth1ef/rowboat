from disco.bot import Plugin
from disco.types.base import Unset
from disco.api.http import APIException
from disco.bot.command import CommandEvent
from disco.gateway.events import GatewayEvent

from rowboat import raven_client
from rowboat.util import MetaException
from rowboat.types import Field
from rowboat.types.guild import PluginsConfig


class SafePluginInterface(object):
    def __init__(self, plugin):
        self.plugin = plugin

    def __getattr__(self, name):
        def wrapped(*args, **kwargs):
            if not self.plugin:
                return None

            return getattr(self.plugin, name)(*args, **kwargs)
        return wrapped


class RavenPlugin(object):
    """
    The RavenPlugin base plugin class manages tracking exceptions on a plugin
    level, by hooking the `handle_exception` function from disco.
    """
    def handle_exception(self, greenlet, event):
        extra = {}

        if isinstance(greenlet.exception, MetaException):
            extra.update(greenlet.exception.metadata)

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
            try:
                extra['event'] = {
                    'name': event.__class__.__name__,
                    'data': event.to_dict(),
                }
            except:
                pass

        raven_client.captureException(exc_info=greenlet.exc_info, extra=extra)


class BasePlugin(RavenPlugin, Plugin):
    """
    A BasePlugin is simply a normal Disco plugin, but aliased so we have more
    control. BasePlugins do not have hooked/altered events, unlike a RowboatPlugin.
    """
    _shallow = True


class RowboatPlugin(RavenPlugin, Plugin):
    """
    A plugin which wraps events to load guild configuration.
    """
    _shallow = True
    global_plugin = False

    def get_safe_plugin(self, name):
        return SafePluginInterface(self.bot.plugins.get(name))

    @classmethod
    def with_config(cls, config_cls):
        def deco(plugin_cls):
            name = plugin_cls.__name__.replace('Plugin', '').lower()
            PluginsConfig._fields[name] = Field(config_cls, default=Unset)
            PluginsConfig._fields[name].name = name
            # PluginsConfig._fields[name].default = None
            return plugin_cls
        return deco

    @property
    def name(self):
        return self.__class__.__name__.replace('Plugin', '').lower()

    def call(self, query, *args, **kwargs):
        plugin_name, method_name = query.split('.', 1)

        plugin = self.bot.plugins.get(plugin_name)
        if not plugin:
            raise Exception('Cannot resolve plugin %s (%s)' % (plugin_name, query))

        method = getattr(plugin, method_name, None)
        if not method:
            raise Exception('Cannot resolve method %s for plugin %s' % (method_name, plugin_name))

        return method(*args, **kwargs)


class CommandResponse(Exception):
    EMOJI = None

    def __init__(self, response):
        if self.EMOJI:
            response = u':{}: {}'.format(self.EMOJI, response)
        self.response = response


class CommandFail(CommandResponse):
    EMOJI = 'no_entry_sign'


class CommandSuccess(CommandResponse):
    EMOJI = 'ballot_box_with_check'
