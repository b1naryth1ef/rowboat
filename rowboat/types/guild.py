import yaml

from disco.types.base import SlottedModel, Field, text, snowflake


def channel(raw):
    if isinstance(raw, basestring) and raw:
        if raw[0] == '#':
            return raw
    return snowflake(raw)


class ModLogConfig(SlottedModel):
    channel = Field(channel)


class PluginsConfig(SlottedModel):
    modlog = Field(ModLogConfig)


class GuildConfig(SlottedModel):
    nickname = Field(text)
    plugins = Field(PluginsConfig)

    @classmethod
    def loads(cls, content, safe=False):
        obj = yaml.load(content)
        return GuildConfig(obj)
