from disco.types.base import SlottedModel, Field, text, snowflake

__all__ = ['SlottedModel', 'Field', 'text', 'snowflake', 'channel']


def channel(raw):
    if isinstance(raw, basestring) and raw:
        if raw[0] == '#':
            return raw[1:]
    return snowflake(raw)
