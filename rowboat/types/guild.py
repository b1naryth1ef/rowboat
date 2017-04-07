from holster.enum import Enum

from rowboat.types import Model, SlottedModel, Field, DictField, text

CooldownMode = Enum(
    'GUILD',
    'CHANNEL',
    'USER',
)


class PluginConfigObj(object):
    client = None


class PluginsConfig(Model):
    def __init__(self, inst, obj):
        self.client = None
        self.load_into(inst, obj)

    @classmethod
    def parse(cls, obj, *args, **kwargs):
        inst = PluginConfigObj()
        cls(inst, obj)
        return inst


class CommandCooldownConfig(SlottedModel):
    mode = Field(CooldownMode, default=CooldownMode.USER)
    limit = Field(int)
    per = Field(int)


class CommandOverrideConfig(SlottedModel):
    disabled = Field(bool, default=False)
    level = Field(int)
    cooldown = Field(CommandCooldownConfig)


class CommandsConfig(SlottedModel):
    prefix = Field(str, default='')
    mention = Field(bool, default=False)
    overrides = DictField(str, CommandOverrideConfig)
    cooldown = Field(CommandCooldownConfig)


class GuildConfig(SlottedModel):
    nickname = Field(text)
    commands = Field(CommandsConfig, default=None, create=False)
    levels = DictField(int, int)
    plugins = Field(PluginsConfig.parse)

    # TODO
    def validate(self):
        pass
