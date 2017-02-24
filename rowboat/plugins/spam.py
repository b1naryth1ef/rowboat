import re
import time

from gevent.lock import Semaphore
from datetime import datetime
from holster.emitter import Priority

from disco.types.guild import VerificationLevel

from rowboat.plugins import RowboatPlugin as Plugin
from rowboat.redis import rdb
from rowboat.plugins.modlog import Actions
from rowboat.util.leakybucket import LeakyBucket
from rowboat.types.plugin import PluginConfig
from rowboat.types import SlottedModel, DictField, Field
from rowboat.models.user import Infraction


# TODO:
#  - detect mention spam
#  - detect normal spam

INVITE_LINK_RE = re.compile(r'(discord.me|discord.gg)(?:/#)?(?:/invite)?/([a-z0-9\-]+)')
URL_RE = re.compile(r'(https?://[^\s]+)')
BAD_WORDS_RE = re.compile('({})'.format('|'.join(open('data/badwords.txt', 'r').read())))


class SubConfig(SlottedModel):
    max_messages_check = Field(bool, desc='Whether to limit the max number of messages during a time period.', default=False)
    max_messages_count = Field(int, desc='The max number of messages per interval this user can send')
    max_messages_interval = Field(int, desc='The interval (in seconds) for the max messages count')

    max_mentions_check = Field(bool, desc='Whether to limit the max number of mentions during a time period.', default=False)
    max_mentions_count = Field(int, desc='The max number of mentions per interval')
    max_mentions_interval = Field(int, desc='The interval (in seconds) for the max mentions count')

    ban_duration = Field(int, desc='Duration of ban (0 == perma) in seconds', default=604800)

    max_mentions_per_message = Field(int, desc='The max number of mentions a single message can have')

    advanced_heuristics = Field(bool, desc='Enable advanced spam detection', default=False)

    def get_max_messages_bucket(self, guild_id):
        if not self.max_messages_check:
            return None

        if not hasattr(self, '_max_messages_bucket'):
            return LeakyBucket(rdb, 'b:msgs:{}:{}'.format(guild_id, '{}'), self.max_messages_count, self.max_messages_interval * 1000)

        return self._max_messages_bucket

    def get_max_mentions_bucket(self, guild_id):
        if not self.max_mentions_check:
            return None

        if not hasattr(self, '_max_mentions_bucket'):
            return LeakyBucket(rdb, 'b:mnts:{}:{}'.format(guild_id, '{}'), self.max_mentions_count, self.max_mentions_interval * 1000)

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

            if violation.event.config.ban_duration == 0:
                Infraction.ban(self, violation.event, violation.member, 'Spam Detected')
            else:
                Infraction.tempban(
                    self,
                    violation.event,
                    violation.member,
                    'Spam Detected',
                    violation.event.config.ban_duration)

            # TODO: clean messages

    def check_message_simple(self, event, member, rule):
        # First, check the max messages rules
        bucket = rule.get_max_messages_bucket(event.guild.id)
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

        bucket = rule.get_max_mentions_bucket(event.guild.id)
        if bucket:
            if not bucket.check(event.author.id, len(event.mentions)):
                raise Violation(
                    event,
                    member,
                    'MAX_MENTIONS',
                    'Too Many Mentions ({} / {}s)'.format(bucket.count(event.author.id), bucket.size(event.author.id)))

    def check_message_advanced(self, event, member, rule):
        member = event.guild.get_member(event.author)
        user_age = (datetime.utcnow() - member.joined_at).total_seconds()

        # Calculate a risk rating based on the contents of the messages
        msg_risk = 0

        # Mention count
        msg_risk += (len(event.mentions) * 250)

        # Each bad word is 25 points
        msg_risk += (len(BAD_WORDS_RE.findall(event.content)) * 250)

        # Invite Links
        msg_risk += (len(INVITE_LINK_RE.findall(event.content)) * 1000)

        # Regular Links
        msg_risk += (
            len(URL_RE.findall(INVITE_LINK_RE.sub('', event.content))) * 500
        )

        rdb.rpush('spam:scores:{}'.format(event.author.id), msg_risk)
        rdb.expire('spam:scores:{}'.format(event.author.id), 60 * 10)
        scores = rdb.lrange('spam:scores:{}'.format(event.author.id), 0, -1)

        score_sum = sum(map(int, scores))
        expected_score = 0

        if event.guild.verification_level is VerificationLevel.HIGH:
            user_age -= 60 * 10

        print user_age, score_sum
        user_age = 60 * 6

        # Gauge the risk by age
        if user_age < (60 * 1):
            if score_sum >= 1000:
                expected_score = 1000
        elif user_age < (60 * 5):
            if score_sum >= 5000:
                expected_score = 5000
        else:
            if score_sum >= 10000:
                expected_score = 10000

        if expected_score:
            self.bot.plugins.get('CorePlugin').send_control_message(
                u'User {} triggered avanced spam detection in channel {}\n  score: {}\n  expected: {}\n  scores: {}'.format(
                    member,
                    event.channel.mention,
                    score_sum,
                    expected_score,
                    len(scores)
                ))

    @Plugin.listen('MessageCreate', priority=Priority.AFTER)
    def on_message_create(self, event):
        if event.author.id == self.state.me.id:
            return

        # Lineralize events by guild ID to prevent spamming events
        if event.guild.id not in self.guild_locks:
            self.guild_locks[event.guild.id] = Semaphore()
        self.guild_locks[event.guild.id].acquire()

        try:
            member = event.guild.get_member(event.author)
            level = int(self.bot.get_level(event.author))

            for rule in event.config.compute_relevant_rules(member, level):
                if rule.advanced_heuristics:
                    self.check_message_advanced(event, member, rule)
                self.check_message_simple(event, member, rule)
        except Violation as v:
            self.violate(v)
        finally:
            self.guild_locks[event.guild.id].release()
