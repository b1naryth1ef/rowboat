import yaml

from rowboat.types import SlottedModel, Field, text
from rowboat.plugins.modlog import ModLogConfig


class PluginsConfig(SlottedModel):
    modlog = Field(ModLogConfig, default=None)


class GuildConfig(SlottedModel):
    nickname = Field(text)
    plugins = Field(PluginsConfig)

    @classmethod
    def loads(cls, content, safe=False):
        obj = yaml.load(content)
        return GuildConfig(obj)
