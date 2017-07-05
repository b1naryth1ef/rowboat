import time
import operator

from gevent.lock import Semaphore
from datetime import datetime, timedelta
from collections import defaultdict
from holster.enum import Enum
from holster.emitter import Priority
from disco.types.guild import VerificationLevel
from disco.util.snowflake import to_datetime

from rowboat.plugins import RowboatPlugin as Plugin
from rowboat.redis import rdb
from rowboat.plugins.modlog import Actions
from rowboat.plugins.censor import URL_RE
from rowboat.util.leakybucket import LeakyBucket
from rowboat.util.stats import timed
from rowboat.types.plugin import PluginConfig
from rowboat.types import SlottedModel, DictField, Field
from rowboat.models.user import Infraction
from rowboat.models.message import Message, TempSpamScore, EMOJI_RE


# TODO: lazy/cached
with open('data/badwords.txt', 'r') as f:
    BAD_WORDS = f.readlines()

PunishmentType = Enum(
    'NONE',
    'MUTE',
    'KICK',
    'TEMPBAN',
    'BAN',
    'TEMPMUTE'
)


class CheckConfig(SlottedModel):
    count = Field(int)
    interval = Field(int)
    meta = Field(dict, default=None)
    punishment = Field(PunishmentType, default=None)
    punishment_duration = Field(int, default=None)


class SubConfig(SlottedModel):
    max_messages = Field(CheckConfig, default=None)
    max_mentions = Field(CheckConfig, default=None)
    max_links = Field(CheckConfig, default=None)
    max_emojis = Field(CheckConfig, default=None)
    max_newlines = Field(CheckConfig, default=None)
    max_attachments = Field(CheckConfig, default=None)

    max_duplicates = Field(CheckConfig, default=None)

    punishment = Field(PunishmentType, default=PunishmentType.NONE)
    punishment_duration = Field(int, default=300)

    clean = Field(bool, default=False)
    clean_count = Field(int, default=100)
    clean_duration = Field(int, default=900)

    advanced = Field(bool, default=False)

    _cached_max_messages_bucket = Field(str, private=True)
    _cached_max_mentions_bucket = Field(str, private=True)
    _cached_max_links_bucket = Field(str, private=True)
    _cached_max_emojis_bucket = Field(str, private=True)
    _cached_max_newlines_bucket = Field(str, private=True)
    _cached_max_attachments_bucket = Field(str, private=True)

    def validate(self):
        if self.clean_duration < 0 or self.clean_duration > 86400:
            raise Exception('Invalid value for `clean_duration` must be between 0 and 86400')

        if self.clean_count < 0 or self.clean_count > 1000:
            raise Exception('Invaliud value for `clean_count` must be between 0 and 1000')

    def get_bucket(self, attr, guild_id):
        obj = getattr(self, attr)
        if not obj or not obj.count or not obj.interval:
            return (None, None)

        bucket = getattr(self, '_cached_{}_bucket'.format(attr), None)
        if not bucket:
            bucket = LeakyBucket(rdb, 'spam:{}:{}:{}'.format(attr, guild_id, '{}'), obj.count, obj.interval * 1000)
            setattr(self, '_cached_{}_bucket'.format(attr), bucket)

        return obj, bucket


class SpamConfig(PluginConfig):
    roles = DictField(str, SubConfig)
    levels = DictField(int, SubConfig)

    def compute_relevant_rules(self, member, level):
        if self.roles:
            if '*' in self.roles:
                yield self.roles['*']

            for rid in member.roles:
                if rid in self.roles:
                    yield self.roles[rid]
                rname = member.guild.roles.get(rid)
                if rname and rname.name in self.roles:
                    yield self.roles[rname.name]

        if self.levels:
            for lvl in self.levels.keys():
                if level <= lvl:
                    yield self.levels[lvl]


class Violation(Exception):
    def __init__(self, rule, check, event, member, label, msg, **info):
        self.rule = rule
        self.check = check
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

            punishment = violation.check.punishment or violation.rule.punishment
            punishment_duration = violation.check.punishment_duration or violation.rule.punishment_duration

            if punishment == PunishmentType.MUTE:
                Infraction.mute(
                    self,
                    violation.event,
                    violation.member,
                    'Spam Detected')
            elif punishment == PunishmentType.TEMPMUTE:
                Infraction.tempmute(
                    self,
                    violation.event,
                    violation.member,
                    'Spam Detected',
                    datetime.utcnow() + timedelta(seconds=punishment_duration))
            elif punishment == PunishmentType.KICK:
                Infraction.kick(
                    self,
                    violation.event,
                    violation.member,
                    'Spam Detected')
            elif punishment == PunishmentType.TEMPBAN:
                Infraction.tempban(
                    self,
                    violation.event,
                    violation.member,
                    'Spam Detected',
                    datetime.utcnow() + timedelta(seconds=punishment_duration))
            elif punishment == PunishmentType.BAN:
                Infraction.ban(
                    self,
                    violation.event,
                    violation.member,
                    'Spam Detected',
                    violation.event.guild)

            # Clean messages if requested
            if punishment != PunishmentType.NONE and violation.rule.clean:
                msgs = Message.select(
                    Message.id,
                    Message.channel_id
                ).where(
                    (Message.guild_id == violation.event.guild.id) &
                    (Message.author_id == violation.member.id) &
                    (Message.timestamp > (datetime.utcnow() - timedelta(seconds=violation.rule.clean_duration)))
                ).limit(violation.rule.clean_count).tuples()

                channels = defaultdict(list)
                for mid, chan in msgs:
                    channels[chan].append(mid)

                for channel, messages in channels.items():
                    channel = self.state.channels.get(channel)
                    if not channel:
                        continue

                    channel.delete_messages(messages)

    def check_duplicate_messages(self, event, member, rule):
        q = [
            (Message.guild_id == event.guild.id),
            (Message.timestamp > (datetime.utcnow() - timedelta(seconds=rule.max_duplicates.interval)))
        ]

        # If we're not checking globally, include the member id
        if not rule.max_duplicates.meta or not rule.max_duplicates.meta.get('global'):
            q.append((Message.author_id == member.id))

        # Grab the previous messages the user sent in this server
        msgs = list(Message.select(
            Message.id,
            Message.content,
        ).where(reduce(operator.and_, q)).order_by(
            Message.timestamp.desc()
        ).limit(50).tuples())

        # Group the messages by their content
        dupes = defaultdict(int)
        for mid, content in msgs:
            if content:
                dupes[content] += 1

        # If any of them are above the max dupes count, trigger a violation
        dupes = [v for k, v in dupes.items() if v > rule.max_duplicates.count]
        if dupes:
            raise Violation(
                rule,
                rule.max_duplicates,
                event,
                member,
                'MAX_DUPLICATES',
                'Too Many Duplicated Messages ({} / {})'.format(
                    sum(dupes),
                    len(dupes)))

    def check_message_simple(self, event, member, rule):
        def check_bucket(name, base_text, func):
            check, bucket = rule.get_bucket(name, event.guild.id)
            if not bucket:
                return

            if not bucket.check(event.author.id, func(event) if callable(func) else func):
                raise Violation(rule, check, event, member,
                    name.upper(),
                    base_text + ' ({} / {}s)'.format(bucket.count(event.author.id), bucket.size(event.author.id)))

        check_bucket('max_messages', 'Too Many Messages', 1)
        check_bucket('max_mentions', 'Too Many Mentions', lambda e: len(e.mentions))
        check_bucket('max_links', 'Too Many Links', lambda e: len(URL_RE.findall(e.message.content)))
        # TODO: unicode emoji too pls
        check_bucket('max_emojis', 'Too Many Emojis', lambda e: len(EMOJI_RE.findall(e.message.content)))
        check_bucket('max_newlines', 'Too Many Newlines', lambda e: e.message.content.count('\n'))
        check_bucket('max_attachments', 'Too Many Attachments', lambda e: len(e.message.attachments))

        if rule.max_duplicates and rule.max_duplicates.interval and rule.max_duplicates.count:
            self.check_duplicate_messages(event, member, rule)

        if rule.advanced:
            self.check_advanced(event, member, rule)

    def check_advanced(self, event, member, rule):
        score = 0

        # CHECK 1
        # Check if the user just exited their quiescent period from guild verification
        #  which means they may have been waiting to spam
        duration_before_talk = 0
        if event.guild.verification_level == VerificationLevel.MEDIUM:
            duration_before_talk = 60 * 5
        elif event.guild.verification_level == VerificationLevel.HIGH:
            duration_before_talk = 60 * 10

        if duration_before_talk:
            duration = (datetime.utcnow() - member.joined_at).seconds
            if duration >= duration_before_talk:
                if (duration - duration_before_talk) < 10:
                    score += 5
                elif (duration - duration_before_talk) < 60:
                    score += 4
                elif (duration - duration_before_talk) < 120:
                    score += 3
                elif (duration - duration_before_talk) < 300:
                    score += 1

        # CHECK 2
        # Check if the users account was created recently, which means they may
        #  have made it just to spam.
        account_age = (datetime.utcnow() - to_datetime(event.author.id)).seconds
        if account_age < 15 * 60:
            score += 15
        elif account_age < 30 * 60:
            score += 10
        elif account_age < 60 * 60:
            score += 5

        # CHECK 3
        # Check if this is the first message sent by the user, perhaps signaling
        #  they just joined to spam
        sent_messages = Message.select().where(
            (Message.guild_id == event.guild.id) &
            (Message.author_id == event.author.id)
        ).count()

        if sent_messages == 0:
            score += 7
        elif sent_messages < 10:
            score += 3

        # CHECK 4
        # For every user mentioned in their message, determine how "important"
        #  or likely to be spammed they are.
        for mention in event.mentions.values():
            member = event.guild.get_member(mention)

            # If the user is an admin of the server, they are likely to be a victim
            if member.permissions.administrator or member.permissions.manage_guild:
                score += 3
            elif member.permissions.ban_members or member.permissions.kick_members:
                score += 1

            # If the user is hoisted, they are likely to be a victim
            if any(i.hoisted for i in map(event.guild.roles.get, member.roles)):
                score += 5

        # CHECK 5
        # Check how many bad words are in the message, generally low-effort spammers
        #  just shove "shock" value content in their message.
        for word in event.content.split(' '):
            if word in BAD_WORDS:
                score += 1

        TempSpamScore.track(event.id, score)
        self.log.info('[spam] advanced detection for %s: %s', event.id, score)

    @Plugin.listen('MessageCreate', priority=Priority.AFTER)
    def on_message_create(self, event):
        if event.author.id == self.state.me.id:
            return

        # Lineralize events by guild ID to prevent spamming events
        if event.guild.id not in self.guild_locks:
            self.guild_locks[event.guild.id] = Semaphore()
        self.guild_locks[event.guild.id].acquire()

        tags = {'guild_id': event.guild.id, 'channel_id': event.channel.id}
        with timed('rowboat.plugin.spam.duration', tags=tags):
            try:
                member = event.guild.get_member(event.author)
                if not member:
                    self.log.warning('Failed to find member for guild id %s and author id %s', (event.guild.id, event.author.id))
                    return

                level = int(self.bot.plugins.get('CorePlugin').get_level(event.guild, event.author))

                # TODO: We should linerialize the work required for all rules in one go,
                #  we repeat all the work in each rule which sucks.

                for rule in event.config.compute_relevant_rules(member, level):
                    self.check_message_simple(event, member, rule)
            except Violation as v:
                self.violate(v)
            finally:
                self.guild_locks[event.guild.id].release()
