import fnmatch

from holster.enum import Enum

from rowboat.types import Model, SlottedModel, Field, DictField, text, raw

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


class CommandOverrideConfig(SlottedModel):
    disabled = Field(bool, default=False)
    level = Field(int)


class CommandsConfig(SlottedModel):
    prefix = Field(str, default='')
    mention = Field(bool, default=False)
    overrides = Field(raw)

    def get_command_override(self, command):
        return rule_matcher(command, self.overrides or [])


class GuildConfig(SlottedModel):
    nickname = Field(text)
    commands = Field(CommandsConfig, default=None, create=False)
    levels = DictField(int, int)
    plugins = Field(PluginsConfig.parse)


class RuleException(Exception):
    pass


_RULES = {
    'gt': ((int, float), lambda a, b: a > b),
    'lt': ((int, float), lambda a, b: a < b),
    'match': ((str, unicode), lambda a, b: fnmatch.fnmatch(a, b)),
}


def get_object_path(obj, path):
    if '.' not in path:
        return getattr(obj, path)
    key, rest = path.split('.', 1)
    return get_object_path(getattr(obj, key), rest)


def rule_matcher(obj, rules):
    for rule in rules:
        for field_name, field_rule in rule.items():
            if field_name == 'out':
                continue

            field_value = get_object_path(obj, field_name)

            if isinstance(field_rule, dict):
                matched = True

                for rule_filter, b in field_rule.items():
                    if rule_filter not in _RULES:
                        raise RuleException('unknown rule filter {}'.format(rule_filter))

                    typs, func = _RULES[rule_filter]
                    if not isinstance(field_value, typs):
                        raise RuleException('invalid type for rule filter, have {} but want {}'.format(type(field_value), typs))

                    if not func(field_value, b):
                        matched = False
                        break

                if not matched:
                    break
            elif field_value != field_rule:
                break
        else:
            yield rule['out']
