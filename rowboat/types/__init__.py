import fnmatch

from disco.types.base import Model, SlottedModel, Field, ListField, DictField, text, snowflake

__all__ = [
    'Model', 'SlottedModel', 'Field', 'ListField', 'DictField', 'text', 'snowflake', 'channel', 'raw',
    'rule_matcher'
]


def raw(obj):
    return obj


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


class RuleException(Exception):
    pass


_FUNCS = {
    'length': lambda a: len(a),
}

_FILTERS = {
    'eq': ((str, unicode, int, float, list, tuple, set), lambda a, b: a == b),
    'gt': ((int, float), lambda a, b: a > b),
    'lt': ((int, float), lambda a, b: a < b),
    'gte': ((int, float), lambda a, b: a >= b),
    'lte': ((int, float), lambda a, b: a <= b),
    'match': ((str, unicode), lambda a, b: fnmatch.fnmatch(a, b)),
    'contains': ((list, tuple, set), lambda a, b: a.contains(b)),
}


def get_object_path(obj, path):
    if '.' not in path:
        return getattr(obj, path)
    key, rest = path.split('.', 1)
    return get_object_path(getattr(obj, key), rest)


def _check_filter(filter_name, filter_data, value):
    if filter_name in _FUNCS:
        new_value = _FUNCS[filter_name](value)
        if isinstance(filter_data, dict):
            return all([_check_filter(k, v, new_value) for k, v in filter_data.items()])
        return new_value == filter_data

    negate = False
    if filter_name.startswith('not_'):
        negate = True
        filter_name = filter_name[4:]

    if filter_name not in _FILTERS:
        raise RuleException('unknown filter {}'.format(filter_name))

    typs, filt = _FILTERS[filter_name]
    if not isinstance(value, typs):
        raise RuleException('invalid type for filter, have {} but want {}'.format(
            type(value), typs,
        ))

    if negate:
        return not filt(value, filter_data)
    return filt(value, filter_data)


def rule_matcher(obj, rules, output_key='out'):
    for rule in rules:
        for field_name, field_rule in rule.items():
            if field_name == output_key:
                continue

            field_value = get_object_path(obj, field_name)

            if isinstance(field_rule, dict):
                field_matched = True

                for rule_filter, b in field_rule.items():
                    field_matched = _check_filter(rule_filter, b, field_value)

                if not field_matched:
                    break
            elif field_value != field_rule:
                break
        else:
            yield rule.get(output_key, True)
