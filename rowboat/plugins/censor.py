import re
import json
import urlparse

from holster.enum import Enum
from disco.util.functional import cached_property

from rowboat.redis import rdb
from rowboat.util import C
from rowboat.plugins import RowboatPlugin as Plugin
from rowboat.types import SlottedModel, Field, ListField, DictField, ChannelField, snowflake
from rowboat.types.plugin import PluginConfig
from rowboat.plugins.modlog import Actions


CensorReason = Enum(
    'INVITE',
    'DOMAIN',
    'WORD',
)

INVITE_LINK_RE = re.compile(r'(discordapp.com/invite|discord.me|discord.gg)(?:/#)?(?:/invite)?/([a-z0-9\-]+)', re.I)
URL_RE = re.compile(r'(https?://[^\s]+)')


class CensorSubConfig(SlottedModel):
    filter_invites = Field(bool, default=True)
    invites_guild_whitelist = ListField(snowflake, default=[])
    invites_whitelist = ListField(str, default=[])
    invites_blacklist = ListField(str, default=[])

    filter_domains = Field(bool, default=True)
    domains_whitelist = ListField(str, default=[])
    domains_blacklist = ListField(str, default=[])

    blocked_words = ListField(str, default=[])
    blocked_tokens = ListField(str, default=[])

    @cached_property
    def blocked_words_re(self):
        return re.compile('({})'.format('|'.join(
            map(re.escape, self.blocked_tokens) +
            map(lambda k: r'\b{}\b'.format(re.escape(k)), self.blocked_words)
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
        self.content = C(event.content)

    @property
    def details(self):
        if self.reason is CensorReason.INVITE:
            if self.ctx['guild']:
                return 'invite `{}` to {}'.format(self.ctx['invite'], C(self.ctx['guild']['name']))
            else:
                return 'invite `{}`'.format(self.ctx['invite'])
        elif self.reason is CensorReason.DOMAIN:
            if self.ctx['hit'] == 'whitelist':
                return 'domain `{}` is not in whitelist'.format(C(self.ctx['domain']))
            else:
                return 'domain `{}` is in blacklist'.format(C(self.ctx['domain']))
        elif self.reason is CensorReason.WORD:
            return 'found blacklisted words `{}`'.format(', '.join(map(C, self.ctx['words'])))


@Plugin.with_config(CensorConfig)
class CensorPlugin(Plugin):
    def compute_relevant_configs(self, event):
        if event.channel_id in event.config.channels:
            yield event.config.channels[event.channel.id]

        if event.config.levels:
            user_level = int(self.bot.plugins.get('CorePlugin').get_level(event.guild, event.author))

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

    @Plugin.listen('MessageCreate')
    def on_message_create(self, event):
        if event.author.id == self.state.me.id:
            return

        configs = list(self.compute_relevant_configs(event))
        if not configs:
            return

        try:
            for config in configs:
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

            self.bot.plugins.get('ModLogPlugin').create_debounce(event, event.author, 'censor')
            event.delete()

    def filter_invites(self, event, config):
        invites = INVITE_LINK_RE.findall(event.content)

        for invite in invites:
            invite_info = self.get_invite_info(invite[1])

            need_whitelist = (config.invites_guild_whitelist or (config.invites_whitelist or not config.invites_blacklist))
            whitelisted = False

            if invite_info and invite_info['guild'].get('id') in config.invites_guild_whitelist:
                whitelisted = True

            if invite[1] in config.invites_whitelist:
                whitelisted = True

            if need_whitelist and not whitelisted:
                raise Censorship(CensorReason.INVITE, event, ctx={
                    'hit': 'whietlist',
                    'invite': invite[1],
                    'guild': invite_info,
                })
            elif config.invites_blacklist and invite in config.invites_blacklist:
                raise Censorship(CensorReason.INVITE, event, ctx={
                    'hit': 'blacklist',
                    'invite': invite[1],
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
