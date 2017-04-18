from datetime import datetime, timedelta
from disco.bot.command import CommandError


UNITS = {
    's': lambda v: timedelta(seconds=v),
    'm': lambda v: timedelta(seconds=v * 60),
    'h': lambda v: timedelta(seconds=v * 60 * 60),
    'd': lambda v: timedelta(seconds=v * 60 * 60 * 24),
    'w': lambda v: timedelta(seconds=v * 60 * 60 * 24 * 7),
}


def parse_duration(raw):
    if not raw:
        raise CommandError('Invalid duration')

    if not raw[-1] in UNITS:
        raise CommandError(u'Invalid duration unit `{}`'.format(raw[-1]))
        return None
    unit = UNITS[raw[-1]]

    negate = False
    if raw[0] == '-':
        negate = True
        raw = raw[1:]

    if not raw[:-1].isdigit():
        raise CommandError(u'Duration must be an integer')
        return None

    value = int(raw[:-1])
    if negate:
        value *= -1

    return datetime.utcnow() + unit(value)
