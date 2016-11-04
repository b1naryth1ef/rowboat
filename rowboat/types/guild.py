import re
import yaml
import time
import urlparse
import requests

from rowboat.redis import rdb
from rowboat.types import SlottedModel, Field, DictField, snowflake, text
from rowboat.plugins.modlog import ModLogConfig
from rowboat.plugins.reactions import ReactionsConfig

ALLOWED_DOMAINS = {
    'github.com',
    'githubusercontent.com',
    'pastebin.com',
    'hastebin.com',
    'gitlab.com',
    'bitbucket.org',
}

GIST_RE = re.compile('https://gist.githubusercontent.com/(.*)/(.*)/raw/.*/(.*)')
GIST_FMT = 'https://gist.githubusercontent.com/{}/{}/raw/{}'


def validate_config_url(url):
    parsed = urlparse.urlparse(url)
    if not any(parsed.netloc.endswith(i) for i in ALLOWED_DOMAINS):
        return None

    # Gists can have the revision in them, so lets strip those
    if parsed.netloc.startswith('gist'):
        match = GIST_RE.match(url)
        if match:
            return GIST_FMT.format(*match.groups())

    return url


class PluginsConfig(SlottedModel):
    modlog = Field(ModLogConfig, default=None)
    reactions = Field(ReactionsConfig, default=None)


class GuildConfig(SlottedModel):
    nickname = Field(text)

    # Command Stuff
    prefix = Field(str)
    mention = Field(bool)

    levels = DictField(str, int)
    permissions = DictField(snowflake, str)
    commands = DictField(str, int)
    plugins = Field(PluginsConfig)

    # TODO
    def validate(self):
        pass

    @classmethod
    def create_from_url(cls, guild_id, url):
        url = validate_config_url(url)
        if not url:
            raise Exception('Invalid Configuration URL')

        # Download and parse the configuration
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        cfg = cls.loads(r.content)

        # Once parsed, track our guild in redis and cache the settings
        rdb.sadd('guilds', guild_id)
        rdb.set('config:{}'.format(guild_id), url)
        rdb.set('config:cached:{}'.format(guild_id), r.content)

        return cfg

    @classmethod
    def load_from_id(cls, gid, fresh=False):
        # If we have a cached copy, and we're not force refreshing, return that
        if rdb.exists('config:cached:{}'.format(gid)) and not fresh:
            return cls.loads(rdb.get('config:cached:{}'.format(gid)))

        url = rdb.get('config:{}'.format(gid))
        r = requests.get(url, timeout=15, params={'_t': time.time()})
        r.raise_for_status()
        cfg = cls.loads(r.content)
        rdb.set('config:cached:{}'.format(gid), r.content)
        return cfg

    @classmethod
    def loads(cls, content):
        obj = yaml.load(content)
        return GuildConfig(obj)
