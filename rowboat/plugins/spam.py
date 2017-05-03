import time

from gevent.lock import Semaphore
from datetime import datetime, timedelta
from collections import defaultdict
from holster.enum import Enum
from holster.emitter import Priority

from rowboat.plugins import RowboatPlugin as Plugin
from rowboat.redis import rdb
from rowboat.plugins.modlog import Actions
from rowboat.util.leakybucket import LeakyBucket
from rowboat.types.plugin import PluginConfig
from rowboat.types import SlottedModel, DictField, Field
from rowboat.models.user import Infraction
from rowboat.models.message import Message


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


class SubConfig(SlottedModel):
    max_messages = Field(CheckConfig, default=None)
    max_mentions = Field(CheckConfig, default=None)

    # TODO
    max_links = Field(CheckConfig, default=None)
    max_emojis = Field(CheckConfig, default=None)
    max_newlines = Field(CheckConfig, default=None)

    punishment = Field(PunishmentType, default=PunishmentType.NONE)
    punishment_duration = Field(int, default=300)

    _max_messages_bucket = Field(str, private=True)
    _max_mentions_bucket = Field(str, private=True)

    def get_max_messages_bucket(self, guild_id):
        if not self.max_messages:
            return None

        if not hasattr(self, '_max_messages_bucket'):
            self._max_messages_bucket = LeakyBucket(rdb, 'b:msgs:{}:{}'.format(guild_id, '{}'), self.max_messages.count, self.max_messages.interval * 1000)

        return self._max_messages_bucket

    def get_max_mentions_bucket(self, guild_id):
        if not self.max_mentions:
            return None

        if not hasattr(self, '_max_mentions_bucket'):
            self._max_mentions_bucket = LeakyBucket(rdb, 'b:mnts:{}:{}'.format(guild_id, '{}'), self.max_mentions.count, self.max_mentions.interval * 1000)

        return self._max_mentions_bucket


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
    def __init__(self, rule, event, member, label, msg, **info):
        self.rule = rule
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

            with self.bot.plugins.get('CorePlugin').send_control_message() as embed:
                embed.title = 'Spam Detected'
                embed.color = 0xfdfd96
                embed.add_field(name='Guild', value=violation.event.guild.name)
                embed.add_field(name='Label', value=violation.label)
                embed.add_field(name='User ID', value=violation.event.member.id)
                embed.add_field(name='User Tag', value=unicode(violation.member))

            if violation.rule.punishment is PunishmentType.MUTE:
                Infraction.mute(
                    self,
                    violation.event,
                    violation.member,
                    'Spam Detected')
            elif violation.rule.punishment in PunishmentType.TEMPMUTE:
                Infraction.tempmute(
                    self,
                    violation.event,
                    violation.member,
                    'Spam Detected',
                    violation.rule.punishment_duration)
            elif violation.rule.punishment is PunishmentType.KICK:
                Infraction.kick(
                    self,
                    violation.event,
                    violation.member,
                    'Spam Detected')
            elif violation.rule.punishment is PunishmentType.TEMPBAN:
                Infraction.tempban(
                    self,
                    violation.event,
                    violation.member,
                    'Spam Detected',
                    violation.rule.punishment_duration)
            else:
                Infraction.ban(
                    self,
                    violation.event,
                    violation.member,
                    'Spam Detected',
                    violation.event.guild)

            # Clean messages (TODO move to Infraction)
            msgs = Message.select(
                Message.id,
                Message.channel_id
            ).where(
                (Message.guild_id == violation.event.guild.id) &
                (Message.author_id == violation.member.id) &
                (Message.timestamp >= datetime.utcnow() - timedelta(hours=1))
            )

            channels = defaultdict(list)

            for msg in msgs:
                channels[msg.channel_id].append(msg.id)

            for channel, messages in channels.items():
                channel = self.state.channels.get(channel)
                if not channel:
                    continue

                channel.delete_messages(messages)

    def check_message_simple(self, event, member, rule):
        # First, check the max messages rules
        bucket = rule.get_max_messages_bucket(event.guild.id)
        if bucket:
            if not bucket.check(event.author.id):
                raise Violation(
                    rule,
                    event,
                    member,
                    'MAX_MESSAGES',
                    'Too Many Messages ({} / {}s)'.format(bucket.count(event.author.id), bucket.size(event.author.id)))

        """
        # Next, check max mentions rules
        if rule.max_mentions_per_message and len(event.mentions) > rule.max_mentions_per_message:
            raise Violation(
                rule,
                event,
                member,
                'MAX_MENTIONS_PER_MESSAGE',
                'Too Many Mentions ({} / {})'.format(len(event.mentions), rule.max_mentions_per_message))
        """

        bucket = rule.get_max_mentions_bucket(event.guild.id)
        if bucket:
            if not bucket.check(event.author.id, len(event.mentions)):
                raise Violation(
                    rule,
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
            member = event.guild.get_member(event.author)
            level = int(self.bot.plugins.get('CorePlugin').get_level(event.guild, event.author))

            for rule in event.config.compute_relevant_rules(member, level):
                self.check_message_simple(event, member, rule)
        except Violation as v:
            self.violate(v)
        finally:
            self.guild_locks[event.guild.id].release()
