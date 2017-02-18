from disco.types.base import Model, SlottedModel, Field, ListField, DictField, text, snowflake

__all__ = ['Model', 'SlottedModel', 'Field', 'ListField', 'DictField', 'text', 'snowflake', 'channel']


def ChannelField(raw):
    # Non-integers must be channel names
    if isinstance(raw, basestring) and raw:
        if raw[0] == '#':
            return raw[1:]
        elif not raw[0].isdigit():
            return raw
    return snowflake(raw)


def UserField(raw):
    return snowflake(raw)
