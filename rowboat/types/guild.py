import re
import yaml
import urlparse
import requests


from rowboat.redis import db
from rowboat.types import SlottedModel, Field, text
from rowboat.plugins.modlog import ModLogConfig

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


class GuildConfig(SlottedModel):
    nickname = Field(text)
    plugins = Field(PluginsConfig)

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
        db.sadd('guilds', guild_id)
        db.set('config:{}'.format(guild_id), url)
        db.set('config:cached:{}'.format(guild_id), r.content)

        return cfg

    @classmethod
    def load_from_id(cls, gid, fresh=False):
        # If we have a cached copy, and we're not force refreshing, return that
        if db.exists('config:cached:{}'.format(gid)) and not fresh:
            return cls.loads(db.get('config:cached:{}'.format(gid)))

        url = db.get('config:{}'.format(gid))
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        cfg = cls.loads(r.content)
        db.set('config:cached:{}'.format(gid), r.content)
        return cfg

    @classmethod
    def loads(cls, content):
        obj = yaml.load(content)
        return GuildConfig(obj)
