from disco.types.base import SlottedModel, Field, ListField, DictField, text, snowflake

__all__ = ['SlottedModel', 'Field', 'ListField', 'DictField', 'text', 'snowflake', 'channel']


def ChannelField(raw):
    # Non-integers must be channel names
    if isinstance(raw, basestring) and raw:
        if raw[0] == '#':
            return raw[1:]
        elif not raw[0].isdigit():
            return raw
    return snowflake(raw)
