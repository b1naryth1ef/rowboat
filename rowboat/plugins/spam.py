import time

from gevent.lock import Semaphore

from holster.emitter import Priority

from rowboat.plugins import RowboatPlugin as Plugin
from rowboat.redis import rdb
from rowboat.plugins.modlog import Actions
from rowboat.util.leakybucket import LeakyBucket
from rowboat.types.plugin import PluginConfig
from rowboat.types import SlottedModel, DictField, Field


# TODO:
#  - detect mention spam
#  - detect normal spam


class SubConfig(SlottedModel):
    max_messages_check = Field(bool, desc='Whether to limit the max number of messages during a time period.', default=False)
    max_messages_count = Field(int, desc='The max number of messages per interval this user can send')
    max_messages_interval = Field(int, desc='The interval (in seconds) for the max messages count')

    max_mentions_check = Field(bool, desc='Whether to limit the max number of mentions during a time period.', default=False)
    max_mentions_count = Field(int, desc='The max number of mentions per interval')
    max_mentions_interval = Field(int, desc='The interval (in seconds) for the max mentions count')

    max_mentions_per_message = Field(int, desc='The max number of mentions a single message can have')

    def max_messages_bucket(self, guild_id):
        if not self.max_messages_check:
            return None

        if not hasattr(self, '_max_messages_bucket'):
            return LeakyBucket(rdb, 'msgs:{}:{}'.format(guild_id, '{}'), self.max_messages_count, self.max_messages_interval * 1000)

        return self._max_messages_bucket

    def max_mentions_bucket(self, guild_id):
        if not self.max_mentions_check:
            return None

        if not hasattr(self, '_max_mentions_bucket'):
            return LeakyBucket(rdb, 'mnts:{}:{}'.format(guild_id, '{}'), self.max_mentions_count, self.max_mentions_interval * 1000)

        return self._max_mentions_bucket


class SpamConfig(PluginConfig):
    roles = DictField(str, SubConfig)
    levels = DictField(int, SubConfig)

    def compute_relevant_rules(self, member, level):
        if self.roles:
            if '*' in self.roles:
                yield self.roles['*']

            for rid in member.roles.keys():
                if rid in self.roles:
                    yield self.roles[rid]
                rname = member.guild.roles.get(rid)
                if rname and rname.name in self.roles:
                    yield self.roles[rname.name]

        if self.levels:
            for lvl in self.levels.keys():
                if lvl <= level:
                    yield self.levels[lvl]


class Violation(Exception):
    def __init__(self, event, member, label, msg, **info):
        self.event = event
        self.member = member
        self.label = label
        self.msg = msg
        self.info = info


@Plugin.with_config(SpamConfig)
class SpamPlugin(Plugin):
    def load(self, ctx):
        super(SpamPlugin, self).load(ctx)
        self.guild_locks = {}

    def violate(self, violation):
        key = 'lv:{e.member.guild_id}:{e.member.id}'.format(e=violation.event)
        last_violated = int(rdb.get(key) or 0)
        rdb.setex('lv:{e.member.guild_id}:{e.member.id}'.format(e=violation.event), int(time.time()), 60)

        if not last_violated > time.time() - 10:
            self.bot.plugins.get('ModLogPlugin').log_action_ext(Actions.SPAM_DEBUG, violation.event, v=violation)
            self.bot.plugins.get('CorePlugin').send_control_message(
                u'Spam detected by {} ({}) in guild {} ({})'.format(
                    violation.member,
                    violation.member.id,
                    violation.event.guild,
                    violation.event.guild.id))

    def check_message(self, event):
        member = event.guild.get_member(event.author)
        level = int(self.bot.get_level(event.author))

        for rule in event.config.compute_relevant_rules(member, level):
            # First, check the max messages rules
            bucket = rule.max_messages_bucket(event.guild.id)
            if bucket:
                if not bucket.check(event.author.id):
                    raise Violation(
                        event,
                        member,
                        'MAX_MESSAGES',
                        'Too Many Messages ({} / {}s)'.format(bucket.count(event.author.id), bucket.size(event.author.id)))

            # Next, check max mentions rules
            if rule.max_mentions_per_message and len(event.mentions) > rule.max_mentions_per_message:
                raise Violation(
                    event,
                    member,
                    'MAX_MENTIONS_PER_MESSAGE',
                    'Too Many Mentions ({} / {})'.format(len(event.mentions), rule.max_mentions_per_message))

            bucket = rule.max_mentions_bucket(event.guild.id)
            if bucket:
                if not bucket.check(event.author.id, len(event.mentions)):
                    raise Violation(
                        event,
                        member,
                        'MAX_MENTIONS',
                        'Too Many Mentions ({} / {}s)'.format(bucket.count(event.author.id), bucket.size(event.author.id)))

    @Plugin.listen('MessageCreate', priority=Priority.AFTER)
    def on_message_create(self, event):
        if event.author.id == self.state.me.id:
            return

        # Lineralize events by guild ID to prevent spamming events
        if event.guild.id not in self.guild_locks:
            self.guild_locks[event.guild.id] = Semaphore()
        self.guild_locks[event.guild.id].acquire()

        try:
            self.check_message(event)
        except Violation as v:
            self.violate(v)
        finally:
            self.guild_locks[event.guild.id].release()

    def calculate_mentions(self, event):
        pass
