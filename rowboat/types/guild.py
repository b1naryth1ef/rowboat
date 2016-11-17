from holster.enum import Enum

from rowboat.types import SlottedModel, Field, DictField, text
from rowboat.plugins.modlog import ModLogConfig
from rowboat.plugins.reactions import ReactionsConfig
from rowboat.plugins.admin import AdminConfig
from rowboat.plugins.utilities import UtilitiesConfig
from rowboat.plugins.pickup import PickupConfig


CooldownMode = Enum(
    'GUILD',
    'CHANNEL',
    'USER',
)


class PluginsConfig(SlottedModel):
    modlog = Field(ModLogConfig, default=None)
    reactions = Field(ReactionsConfig, default=None)
    admin = Field(AdminConfig, default=None)
    utilities = Field(UtilitiesConfig, default=None)
    pickup = Field(PickupConfig, default=None)


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
    # TODO: role name support
    levels = DictField(int, int)
    plugins = Field(PluginsConfig)

    # TODO
    def validate(self):
        pass
