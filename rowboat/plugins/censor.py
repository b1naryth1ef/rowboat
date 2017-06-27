import re
import json
import urlparse

from holster.enum import Enum
from disco.util.functional import cached_property
from disco.util.sanitize import S

from rowboat.redis import rdb
from rowboat.util.stats import timed
from rowboat.util.zalgo import ZALGO_RE
from rowboat.plugins import RowboatPlugin as Plugin
from rowboat.types import SlottedModel, Field, ListField, DictField, ChannelField, snowflake
from rowboat.types.plugin import PluginConfig
from rowboat.models.message import Message
from rowboat.plugins.modlog import Actions


CensorReason = Enum(
    'INVITE',
    'DOMAIN',
    'WORD',
    'ZALGO',
)

INVITE_LINK_RE = re.compile(r'(discordapp.com/invite|discord.me|discord.gg)(?:/#)?(?:/invite)?/([a-z0-9\-]+)', re.I)
URL_RE = re.compile(r'(https?://[^\s]+)')


class CensorSubConfig(SlottedModel):
    filter_zalgo = Field(bool, default=True)

    filter_invites = Field(bool, default=True)
    invites_guild_whitelist = ListField(snowflake, default=[])
    invites_whitelist = ListField(unicode, default=[])
    invites_blacklist = ListField(unicode, default=[])

    filter_domains = Field(bool, default=True)
    domains_whitelist = ListField(unicode, default=[])
    domains_blacklist = ListField(unicode, default=[])

    blocked_words = ListField(unicode, default=[])
    blocked_tokens = ListField(unicode, default=[])

    @cached_property
    def blocked_words_re(self):
        return re.compile(u'({})'.format(u'|'.join(
            map(re.escape, self.blocked_tokens) +
            map(lambda k: u'\\b{}\\b'.format(re.escape(k)), self.blocked_words)
        )), re.I)


class CensorConfig(PluginConfig):
    levels = DictField(int, CensorSubConfig)
    channels = DictField(ChannelField, CensorSubConfig)


# It's bad kids!
class Censorship(Exception):
    def __init__(self, reason, event, ctx):
        self.reason = reason
        self.event = event
        self.ctx = ctx
        self.content = S(event.content, escape_codeblocks=True)

    @property
    def details(self):
        if self.reason is CensorReason.INVITE:
            if self.ctx['guild']:
                return u'invite `{}` to {}'.format(self.ctx['invite'], S(self.ctx['guild']['name'], escape_codeblocks=True))
            else:
                return u'invite `{}`'.format(self.ctx['invite'])
        elif self.reason is CensorReason.DOMAIN:
            if self.ctx['hit'] == 'whitelist':
                return u'domain `{}` is not in whitelist'.format(S(self.ctx['domain'], escape_codeblocks=True))
            else:
                return u'domain `{}` is in blacklist'.format(S(self.ctx['domain'], escape_codeblocks=True))
        elif self.reason is CensorReason.WORD:
            return u'found blacklisted words `{}`'.format(
                u', '.join([S(i, escape_codeblocks=True) for i in self.ctx['words']]))
        elif self.reason is CensorReason.ZALGO:
            return u'found zalgo at position `{}` in text'.format(
                self.ctx['position']
            )


@Plugin.with_config(CensorConfig)
class CensorPlugin(Plugin):
    def compute_relevant_configs(self, event, author):
        if event.channel_id in event.config.channels:
            yield event.config.channels[event.channel.id]

        if event.config.levels:
            user_level = int(self.bot.plugins.get('CorePlugin').get_level(event.guild, author))

            for level, config in event.config.levels.items():
                if user_level <= level:
                    yield config

    def get_invite_info(self, code):
        if rdb.exists('inv:{}'.format(code)):
            return json.loads(rdb.get('inv:{}'.format(code)))

        try:
            obj = self.client.api.invites_get(code)
        except:
            return

        obj = {
            'id': obj.guild.id,
            'name': obj.guild.name,
            'icon': obj.guild.icon
        }

        # Cache for 12 hours
        rdb.setex('inv:{}'.format(code), json.dumps(obj), 43200)
        return obj

    @Plugin.listen('MessageUpdate')
    def on_message_update(self, event):
        try:
            msg = Message.get(id=event.id)
        except Message.DoesNotExist:
            self.log.warning('Not censoring MessageUpdate for id %s, %s, no stored message', event.channel_id, event.id)
            return

        if not event.content:
            return

        return self.on_message_create(
            event,
            author=event.guild.get_member(msg.author_id))

    @Plugin.listen('MessageCreate')
    def on_message_create(self, event, author=None):
        author = author or event.author

        if author.id == self.state.me.id:
            return

        configs = list(self.compute_relevant_configs(event, author))
        if not configs:
            return

        tags = {'guild_id': event.guild.id, 'channel_id': event.channel.id}
        with timed('rowboat.plugin.censor.duration', tags=tags):
            try:
                # TODO: perhaps imap here? how to raise exception then?
                for config in configs:
                    if config.filter_zalgo:
                        self.filter_zalgo(event, config)

                    if config.filter_invites:
                        self.filter_invites(event, config)

                    if config.filter_domains:
                        self.filter_domains(event, config)

                    if config.blocked_words:
                        self.filter_blocked_words(event, config)
            except Censorship as c:
                self.bot.plugins.get('ModLogPlugin').log_action_ext(
                    Actions.CENSORED,
                    event,
                    c=c)

                self.bot.plugins.get('ModLogPlugin').create_debounce(event, author.id, 'censor')
                event.delete()

    def filter_zalgo(self, event, config):
        s = ZALGO_RE.search(event.content)
        if s:
            raise Censorship(CensorReason.ZALGO, event, ctx={
                'position': s.start()
            })

    def filter_invites(self, event, config):
        invites = INVITE_LINK_RE.findall(event.content)

        for _, invite in invites:
            invite_info = self.get_invite_info(invite)

            need_whitelist = (config.invites_guild_whitelist or (config.invites_whitelist or not config.invites_blacklist))
            whitelisted = False

            if invite_info and invite_info.get('id') in config.invites_guild_whitelist:
                whitelisted = True

            if invite in config.invites_whitelist or invite.lower() in config.invites_whitelist:
                whitelisted = True

            if need_whitelist and not whitelisted:
                raise Censorship(CensorReason.INVITE, event, ctx={
                    'hit': 'whietlist',
                    'invite': invite,
                    'guild': invite_info,
                })
            elif config.invites_blacklist and (invite in config.invites_blacklist or invite.lower() in config.invites_blacklist):
                raise Censorship(CensorReason.INVITE, event, ctx={
                    'hit': 'blacklist',
                    'invite': invite,
                    'guild': invite_info,
                })

    def filter_domains(self, event, config):
        urls = URL_RE.findall(INVITE_LINK_RE.sub('', event.content))

        for url in urls:
            try:
                parsed = urlparse.urlparse(url)
            except:
                continue

            if (config.domains_whitelist or not config.domains_blacklist) and parsed.netloc not in config.domains_whitelist:
                raise Censorship(CensorReason.DOMAIN, event, ctx={
                    'hit': 'whitelist',
                    'url': url,
                    'domain': parsed.netloc,
                })
            elif config.domains_blacklist and parsed.netloc in config.domains_blacklist:
                raise Censorship(CensorReason.DOMAIN, event, ctx={
                    'hit': 'blacklist',
                    'url': url,
                    'domain': parsed.netloc
                })

    def filter_blocked_words(self, event, config):
        blocked_words = config.blocked_words_re.findall(event.content)

        if blocked_words:
            raise Censorship(CensorReason.WORD, event, ctx={
                'words': blocked_words,
            })
