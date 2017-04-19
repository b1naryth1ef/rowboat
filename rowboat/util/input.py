from datetime import datetime, timedelta
from disco.bot.command import CommandError


UNITS = {
    's': lambda v: v,
    'm': lambda v: v * 60,
    'h': lambda v: v * 60 * 60,
    'd': lambda v: v * 60 * 60 * 24,
    'w': lambda v: v * 60 * 60 * 24 * 7,
}


def parse_duration(raw):
    if not raw:
        raise CommandError('Invalid duration')

    value = 0
    digits = ''

    for char in raw:
        if char.isdigit():
            digits += char
            continue

        if char not in UNITS:
            raise CommandError('Invalid duration')

        value += UNITS[char](int(digits))
        digits = ''

    return datetime.utcnow() + timedelta(seconds=value)
