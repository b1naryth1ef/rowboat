import unittest

from rowboat.types import rule_matcher


class SubObject(object):
    key = 'value'


class TestObject(object):
    name = 'test'
    group = 'lol'
    sub = SubObject()

    lmao = [1, 2, 3]


class TestRuleMatcher(unittest.TestCase):
    def test_basic_rules(self):
        rules = [
            {'sub.key': 'value', 'out': 1},
            {'name': 'test', 'out': 2},
            {'name': {'length': 4}, 'out': 3},
            {'group': 'lol', 'out': 4},
            {'group': 'wtf', 'out': 5},
            {'name': {'length': 5}, 'out': 6},
        ]

        matches = list(rule_matcher(TestObject(), rules))
        self.assertEqual(matches, [1, 2, 3, 4])

    def test_catch_all(self):
        rules = [
            {'out': 1}
        ]

        matches = list(rule_matcher(TestObject(), rules))
        self.assertEqual(matches, [1])
