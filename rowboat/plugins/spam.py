import re
import time
import xxhash
import requests

from gevent.lock import Semaphore
from datetime import datetime, timedelta
from collections import defaultdict
from holster.enum import Enum
from holster.emitter import Priority

from google.cloud import vision
from google.cloud.vision.likelihood import Likelihood

from disco.types.guild import VerificationLevel

from rowboat.plugins import RowboatPlugin as Plugin
from rowboat.redis import rdb
from rowboat.plugins.modlog import Actions
from rowboat.util.leakybucket import LeakyBucket
from rowboat.types.plugin import PluginConfig
from rowboat.types import SlottedModel, DictField, Field
from rowboat.models.user import Infraction
from rowboat.models.message import Message


# TODO:
#  - detect mention spam
#  - detect normal spam


INVITE_LINK_RE = re.compile(r'(discord.me|discord.gg)(?:/#)?(?:/invite)?/([a-z0-9\-]+)')
URL_RE = re.compile(r'(https?://[^\s]+)')
BAD_WORDS_RE = re.compile('({})'.format('|'.join(open('data/badwords.txt', 'r').read())))

PunishmentType = Enum(
    'NONE',
    'MUTE',
    'KICK',
    'TEMPBAN',
    'BAN'
)


class CheckConfig(SlottedModel):
    count = Field(int)
    interval = Field(int)
    punishment = Field(PunishmentType, default=PunishmentType.NONE)
    punishment_duration = Field(int, default=300)


class SubConfig(SlottedModel):
    max_messages = Field(CheckConfig, default=None)
    max_mentions = Field(CheckConfig, default=None)
    max_links = Field(CheckConfig, default=None)
    max_emojis = Field(CheckConfig, default=None)
    max_newlines = Field(CheckConfig, default=None)

    max_mentions_message = Field(CheckConfig, default=None)
    max_links_message = Field(CheckConfig, default=None)
    max_emojis_message = Field(CheckConfig, default=None)
    max_newlines_message = Field(CheckConfig, default=None)

    # TODO: move to censor
    block_nsfw_images = Field(bool, default=True)

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
            self.bot.plugins.get('CorePlugin').send_control_message(
                u'Spam ({}) detected by {} ({}) in guild {} ({})'.format(
                    violation.label,
                    violation.member,
                    violation.member.id,
                    violation.event.guild.name,
                    violation.event.guild.id))

            if violation.rule.punishment is PunishmentType.MUTE:
                pass
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

        # Next, check max mentions rules
        if rule.max_mentions_per_message and len(event.mentions) > rule.max_mentions_per_message:
            raise Violation(
                rule,
                event,
                member,
                'MAX_MENTIONS_PER_MESSAGE',
                'Too Many Mentions ({} / {})'.format(len(event.mentions), rule.max_mentions_per_message))

        bucket = rule.get_max_mentions_bucket(event.guild.id)
        if bucket:
            if not bucket.check(event.author.id, len(event.mentions)):
                raise Violation(
                    rule,
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

    @property
    def vision_client(self):
        if not hasattr(self, '_vision_client'):
            self._vision_client = vision.Client()
            # self._vision_client = vision.Client.from_service_account_json(os.getenv('VISION_KEY')

        return self._vision_client

    def check_nsfw_images(self, event, member, rule):
        influx = self.get_safe_plugin('InfluxPlugin')

        def is_bad_image(url):
            try:
                data = requests.get(url)
                hsh = xxhash.xxh32()
                hsh.update(data.content)
                key = 'nsfw:{}'.format(hsh.digest())

                if rdb.exists(key):
                    influx.write_point('spam.nsfw.check_image', {
                        'cached': True,
                    })
                    if int(rdb.get(key)):
                        return True
                    return False
            except:
                self.log.exception('Failed to check image at url %s', url)
                return False

            image = self.vision_client.image(content=data.content)
            safe = image.detect_safe_search()
            self.log.info('Image safe search for %s: %s / %s / %s', url, safe.adult, safe.medical, safe.violence)

            value = (
                safe.adult in [Likelihood.LIKELY, Likelihood.VERY_LIKELY] or
                safe.medical in [Likelihood.LIKELY, Likelihood.VERY_LIKELY] or
                safe.violence in [Likelihood.LIKELY, Likelihood.VERY_LIKELY]
            )

            influx.write_point('spam.nsfw.check_image', {
                'cached': False,
                'value': value,
            })

            rdb.set(key, int(value))
            return value

        urls = [
            i.url for i in event.attachments.values() if i.url
        ] + [
            i.image.url for i in event.embeds if i.image and i.image.url
        ] + URL_RE.findall(INVITE_LINK_RE.sub('', event.content))

        if not urls:
            return

        for url in urls:
            if is_bad_image(url):
                raise Violation(
                    rule,
                    event,
                    member,
                    'NSFW_IMAGE',
                    'NSFW Image Posted')

    @Plugin.listen('MessageCreate', priority=Priority.AFTER)
    def on_message_create(self, event):
        # TODO: temp disabled while I rewrite
        return
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
                # if rule.advanced_heuristics:
                #     self.check_message_advanced(event, member, rule)

                if rule.nsfw_images:
                    self.check_nsfw_images(event, member, rule)

                self.check_message_simple(event, member, rule)
        except Violation as v:
            self.violate(v)
        finally:
            self.guild_locks[event.guild.id].release()
