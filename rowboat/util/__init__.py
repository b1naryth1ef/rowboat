import re
import yaml
from collections import OrderedDict

from datetime import datetime
from gevent.local import local

# Invisible space that can be used to escape mentions
ZERO_WIDTH_SPACE = u'\u200B'

# Replacement grave accent that can be used to escape codeblocks
MODIFIER_GRAVE_ACCENT = u'\u02CB'


def ordered_load(stream, Loader=yaml.Loader, object_pairs_hook=OrderedDict):
    class OrderedLoader(Loader):
        pass

    def construct_mapping(loader, node):
        loader.flatten_mapping(node)
        return object_pairs_hook(loader.construct_pairs(node))
    OrderedLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping)
    return yaml.load(stream, OrderedLoader)


INVITE_DOMAIN_RE = re.compile(r'(discord.gg|discordapp.com/invite)')


def C(txt, codeblocks=False):
    # Do some basic safety checks:
    txt = txt.replace('@', '@' + ZERO_WIDTH_SPACE)

    if codeblocks:
        txt = escape_codeblocks(txt)

    return INVITE_DOMAIN_RE.sub('\g<0>' + ZERO_WIDTH_SPACE, txt)


def escape_codeblocks(txt):
    return txt.replace('`', MODIFIER_GRAVE_ACCENT)


class LocalProxy(object):
    def __init__(self):
        self.local = local()

    def set(self, other):
        self.local.obj = other

    def get(self):
        return self.local.obj

    def __getattr__(self, attr):
        return getattr(self.local.obj, attr)


class MetaException(Exception):
    def __init__(self, msg, metadata=None):
        self.msg = msg
        self.metadata = metadata
        super(MetaException, self).__init__(msg)


def default_json(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    return TypeError('Type %s is not serializable' % type(obj))
