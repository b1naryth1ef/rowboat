import re
import six
import time
import pytz
import gevent
import string
import operator
import humanize

from holster.enum import Enum
from holster.emitter import Priority
from datetime import datetime
from collections import defaultdict
from gevent.lock import Semaphore
from gevent.event import Event

from disco.bot import CommandLevels
from disco.types import UNSET
from disco.api.http import APIException
from disco.util.functional import cached_property
from disco.util.snowflake import to_unix, to_datetime
from disco.util.sanitize import S

from rowboat.plugins import RowboatPlugin as Plugin
from rowboat.types import SlottedModel, Field, ListField, DictField, ChannelField, snowflake
from rowboat.types.plugin import PluginConfig
from rowboat.models.message import Message, MessageArchive
from rowboat.models.guild import Guild
from rowboat.util import ordered_load, MetaException


Actions = Enum()

COLORS = {
    'red': 0xff0033,
    'orange': 0xff7700,
    'green': 0x009a44,
}

URL_REGEX = re.compile(r'(https?://[^\s]+)')


class ModLogPump(object):
    def __init__(self, channel, max_actions, action_time):
        self.channel = channel
        self.action_time = action_time

        self._have = Event()
        self._buffer = []
        self._lock = Semaphore(max_actions)
        self._emitter = gevent.spawn(self._emit_loop)

    def _get_next_message(self):
        data = ''

        while self._buffer:
            payload = self._buffer.pop(0)
            if len(data) + len(payload) > 2000:
                break
            data += '\n'
            data += payload

        return data

    def _emit_loop(self):
        while True:
            self._have.wait()

            try:
                self._emit()
            except APIException as e:
                # If send message is disabled, backoff (we'll drop events but
                #  thats ok)
                if e.code == 40004:
                    gevent.sleep(5)

            if not len(self._buffer):
                self._have.clear()

    def _emit(self):
        self._lock.acquire()
        msg = self._get_next_message()
        if not msg:
            self._lock.release()
            return
        self.channel.send_message(msg)
        gevent.spawn(self._emit_unlock)

    def _emit_unlock(self):
        gevent.sleep(self.action_time)
        self._lock.release()

    def add_message(self, payload):
        self._buffer.append(payload)
        self._have.set()


def filter_urls(content):
    return URL_REGEX.sub(r'<\1>', content)


class ChannelConfig(SlottedModel):
    compact = Field(bool, default=True)
    include = ListField(Actions)
    exclude = ListField(Actions)
    rich = ListField(Actions)

    timestamps = Field(bool, default=False)
    timezone = Field(str, default='US/Eastern')

    @cached_property
    def tz(self):
        return pytz.timezone(self.timezone)

    @cached_property
    def subscribed(self):
        include = set(self.include if self.include else Actions.attrs)
        exclude = set(self.exclude if self.exclude else [])
        return include - exclude


class CustomFormat(SlottedModel):
    emoji = Field(str, default=None)
    format = Field(str, default=None)


class ModLogConfig(PluginConfig):
    resolved = Field(bool, default=False, private=True)

    ignored_users = ListField(snowflake)
    ignored_channels = ListField(snowflake)
    custom = DictField(str, CustomFormat)

    channels = DictField(ChannelField, ChannelConfig)
    new_member_threshold = Field(int, default=(15 * 60))

    _custom = DictField(dict, private=True)
    _channels = DictField(ChannelConfig, private=True)

    @cached_property
    def subscribed(self):
        return reduce(operator.or_, (i.subscribed for i in self.channels.values())) if self.channels else set()


class Formatter(string.Formatter):
    def convert_field(self, value, conversion):
        if conversion == 'z':
            return S(unicode(value), escape_mentions=False, escape_codeblocks=True)
        return unicode(value)


@Plugin.with_config(ModLogConfig)
class ModLogPlugin(Plugin):
    fmt = Formatter()

    def load(self, ctx):
        if not Actions.attrs:
            self.action_simple = {}

            with open('data/actions_simple.yaml') as f:
                simple = ordered_load(f.read())

            for k, v in simple.items():
                self.register_action(k, v)
        else:
            self.action_simple = ctx['action_simple']

        self.debounce = ctx.get('debounce', defaultdict(lambda: defaultdict(dict)))
        self.hushed = {}
        self.pumps = {}

        super(ModLogPlugin, self).load(ctx)

    def unload(self, ctx):
        ctx['action_simple'] = self.action_simple
        ctx['debounce'] = self.debounce
        super(ModLogPlugin, self).unload(ctx)

    def create_debounce(self, event, user_id, typ, **kwargs):
        kwargs.update({
            'type': typ,
            'time': time.time(),
        })

        self.debounce[event.guild.id][user_id][typ] = kwargs

    def pop_debounce(self, guild_id, user_id, typ):
        obj = self.get_debounce(guild_id, user_id, typ)
        self.delete_debounce(guild_id, user_id, typ)
        return obj

    def get_debounce(self, guild_id, user_id, typ):
        return self.debounce[guild_id][user_id].get(typ)

    def delete_debounce(self, guild_id, user_id, typ):
        if typ in self.debounce[guild_id][user_id]:
            del self.debounce[guild_id][user_id][typ]

    def resolve_channels(self, event, config):
        channels = {}
        for key, channel in config.channels.items():
            if isinstance(key, int):
                chan = event.guild.channels.select_one(id=key)
            else:
                chan = event.guild.channels.select_one(name=key)

            if not chan:
                raise MetaException('Failed to ModLog.resolve_channels', {
                    'config_channels': list(config.channels.keys()),
                    'guild_channels': list(event.guild.channels.keys()),
                })
            channels[chan.id] = channel
        config._channels = channels

        config._custom = None
        if config.custom and event.rowboat_guild.is_whitelisted(Guild.WhitelistFlags.MODLOG_CUSTOM_FORMAT):
            custom = {}
            for action, override in config.custom.items():
                action = Actions.get(action)
                if not action:
                    continue

                custom[action] = override.to_dict()
                if not custom[action].get('emoji'):
                    custom[action]['emoji'] = self.action_simple[action]['emoji']

            config._custom = custom

        config.resolved = True

    def register_action(self, name, simple):
        action = Actions.add(name)
        self.action_simple[action] = simple

    def log_action_ext(self, action, event, **details):
        assert hasattr(event.base_config.plugins, 'modlog')
        return self.log_action_raw(action, event, event.guild, getattr(event.base_config.plugins, 'modlog'), **details)

    def log_action(self, action, event, **details):
        return self.log_action_raw(action, event, event.guild, event.config.get(), **details)

    def log_action_raw(self, action, event, guild, config, **details):
        if not config:
            return

        if not config.resolved:
            self.resolve_channels(event, config)

        if not {action} & config.subscribed:
            return

        def generate_simple(chan_config):
            info = self.action_simple.get(action)

            if config._custom:
                if action in config._custom:
                    info = config._custom[action]

            contents = self.fmt.format(six.text_type(info['format']), e=event, **details)

            msg = u':{}: {}'.format(
                info['emoji'],
                S(contents),
            )

            if chan_config.timestamps:
                ts = pytz.utc.localize(datetime.utcnow()).astimezone(chan_config.tz)
                msg = '`[{}]` '.format(ts.strftime('%H:%M:%S')) + msg

            if len(msg) > 2000:
                msg = msg[0:1997] + '...'

            return msg

        for channel_id, chan_config in config._channels.items():
            if not {action} & chan_config.subscribed:
                continue

            msg = generate_simple(chan_config)

            if channel_id not in self.pumps:
                self.pumps[channel_id] = ModLogPump(
                    self.state.channels.get(channel_id), 5, 1.5
                )
            self.pumps[channel_id].add_message(msg)

    @Plugin.command('hush', group='modlog', level=CommandLevels.ADMIN)
    def command_hush(self, event):
        if event.guild.id in self.hushed:
            return event.msg.reply(':warning: modlog is already hushed')

        self.hushed[event.guild.id] = True
        event.msg.reply(':white_check_mark: modlog has been hushed, do your dirty work in peace')

    @Plugin.command('unhush', group='modlog', level=CommandLevels.ADMIN)
    def command_unhush(self, event):
        if event.guild.id not in self.hushed:
            return event.msg.reply(':warning: modlog is not hushed')

        del self.hushed[event.guild.id]
        event.msg.reply(':white_check_mark: modlog has been unhushed, shhhhh... nobody saw anything')

    @Plugin.schedule(120)
    def cleanup_debounce(self):
        for obj in six.itervalues(self.debounce):
            # Copy items so we can mutate
            for uid, uobj in list(six.iteritems(obj)):
                for typ, tobj in list(six.iteritems(uobj)):

                    if tobj['time'] + 30 > time.time():
                        del uobj[typ]

                        if not uobj:
                            del obj[uid]

    @Plugin.listen('ChannelCreate')
    def on_channel_create(self, event):
        self.log_action(Actions.CHANNEL_CREATE, event)

    @Plugin.listen('ChannelDelete')
    def on_channel_delete(self, event):
        self.log_action(Actions.CHANNEL_DELETE, event)

    @Plugin.listen('GuildBanAdd')
    def on_guild_ban_add(self, event):
        if event.user.id in self.debounce[event.guild.id]:
            debounce = self.get_debounce(event.guild.id, event.user.id, 'ban_reason')

            if debounce:
                if debounce['temp']:
                    if debounce['expires']:
                        self.log_action(Actions.GUILD_TEMPBAN_ADD,
                            event,
                            actor=debounce['actor'],
                            expires=debounce['expires'],
                            reason=debounce['reason'])
                    else:
                        self.log_action(Actions.GUILD_SOFTBAN_ADD,
                            event,
                            actor=debounce['actor'],
                            reason=debounce['reason'])
                else:
                    self.log_action(Actions.GUILD_BAN_ADD_REASON,
                        event,
                        actor=debounce['actor'],
                        reason=debounce['reason'])
        else:
            self.log_action(Actions.GUILD_BAN_ADD, event)

        self.create_debounce(event, event.user.id, 'ban')

    @Plugin.listen('GuildBanRemove')
    def on_guild_ban_remove(self, event):
        # Check for debounce to avoid unban notis on softban
        debounce = self.get_debounce(event.guild_id, event.user.id, 'ban_reason')

        if debounce and debounce['temp'] and not debounce['expires']:
            return

        self.log_action(Actions.GUILD_BAN_REMOVE, event)

    @Plugin.listen('GuildMemberAdd')
    def on_guild_member_add(self, event):
        if event.user.id in self.debounce[event.guild.id]:
            del self.debounce[event.guild.id][event.user.id]

        created = humanize.naturaltime(datetime.utcnow() - to_datetime(event.user.id))
        new = (
            event.config.new_member_threshold and
            (time.time() - to_unix(event.user.id)) < event.config.new_member_threshold
        )

        self.log_action(Actions.GUILD_MEMBER_ADD, event, new=' :new:' if new else '', created=created)

    @Plugin.listen('GuildMemberRemove')
    def on_guild_member_remove(self, event):
        debounce = self.get_debounce(event.guild.id, event.user.id, 'kick')

        if debounce:
            self.log_action(Actions.GUILD_MEMBER_KICK, event,
                actor=debounce['actor'],
                reason=debounce['reason'])
            return

        self.log_action(Actions.GUILD_MEMBER_REMOVE, event)

    @Plugin.listen('GuildRoleCreate')
    def on_guild_role_create(self, event):
        self.log_action(Actions.GUILD_ROLE_CREATE, event)

    @Plugin.listen('GuildRoleDelete', priority=Priority.BEFORE)
    def on_guild_role_delete(self, event):
        pre_role = event.guild.roles.get(event.role_id)
        self.log_action(Actions.GUILD_ROLE_DELETE, event, pre_role=pre_role)

    @Plugin.listen('GuildMemberUpdate', priority=Priority.BEFORE)
    def on_guild_member_update(self, event):
        pre_member = event.guild.members.get(event.id)

        if not pre_member:
            return

        # TODO: server mute/deafen

        # Debounce member persist restores
        debounce = self.get_debounce(event.guild.id, event.user.id, 'restore')
        self.delete_debounce(event.guild.id, event.user.id, 'restore')

        if (pre_member.nick or event.nick) and pre_member.nick != event.nick:
            if not pre_member.nick:
                if not debounce:
                    self.log_action(
                        Actions.ADD_NICK,
                        event,
                        nickname=event.nick)
            elif not event.nick:
                self.log_action(
                    Actions.RMV_NICK,
                    event,
                    nickname=pre_member.nick)
            else:
                self.log_action(
                    Actions.CHANGE_NICK,
                    event,
                    before=pre_member.nick or '<NO_NICK>',
                    after=event.nick or '<NO_NICK>')

        pre_roles = set(pre_member.roles)
        post_roles = set(event.roles)
        if pre_roles != post_roles:
            added = post_roles - pre_roles
            removed = pre_roles - post_roles

            if not debounce:
                mute_debounce = self.pop_debounce(event.guild.id, event.user.id, 'muted')

                for role in filter(bool, map(event.guild.roles.get, added)):
                    if mute_debounce and role.id == mute_debounce['role']:
                        if mute_debounce['expires_at']:
                            self.log_action(
                                Actions.MEMBER_TEMP_MUTED,
                                event,
                                actor=mute_debounce['actor'],
                                reason=mute_debounce['reason'],
                                expires_at=mute_debounce['expires_at'])
                        else:
                            self.log_action(
                                Actions.MEMBER_MUTED,
                                event,
                                actor=mute_debounce['actor'],
                                reason=mute_debounce['reason'])
                    else:
                        role_add_debounce = self.pop_debounce(event.guild.id, event.user.id, 'add_role')
                        if role_add_debounce:
                            self.log_action(Actions.GUILD_MEMBER_ROLES_ADD_REASON,
                                event,
                                role=role,
                                actor=role_add_debounce['actor'],
                                reason=role_add_debounce['reason'])
                        else:
                            self.log_action(Actions.GUILD_MEMBER_ROLES_ADD, event, role=role)

            unmute_debounce = self.pop_debounce(event.guild.id, event.user.id, 'unmuted')

            for role in filter(bool, map(event.guild.roles.get, removed)):
                if unmute_debounce and role.id in unmute_debounce['roles']:
                    self.log_action(Actions.MEMBER_UNMUTED, event, actor=unmute_debounce['actor'])
                else:
                    role_rmv_debounce = self.pop_debounce(event.guild.id, event.user.id, 'remove_role')
                    if role_rmv_debounce:
                        self.log_action(Actions.GUILD_MEMBER_ROLES_RMV_REASON,
                            event,
                            role=role,
                            actor=role_rmv_debounce['actor'],
                            reason=role_rmv_debounce['reason'])
                    else:
                        self.log_action(Actions.GUILD_MEMBER_ROLES_RMV, event, role=role)

    @Plugin.listen('PresenceUpdate', priority=Priority.BEFORE, metadata={'global_': True})
    def on_presence_update(self, event):
        plugin = self.bot.plugins.get('CorePlugin')
        if not plugin or not event.user:
            return

        subscribed_guilds = defaultdict(list)

        for guild_id, config in plugin.guilds.items():
            guild = self.state.guilds.get(guild_id)
            if not guild:
                continue

            if event.user.id not in guild.members:
                continue

            config = config.get_config()
            if not config.plugins or not config.plugins.modlog:
                continue

            if event.user.id in config.plugins.modlog.ignored_users:
                continue

            if {Actions.CHANGE_USERNAME} & config.plugins.modlog.subscribed:
                subscribed_guilds[Actions.CHANGE_USERNAME].append((guild, config))

        if not len(subscribed_guilds):
            return

        pre_user = self.state.users.get(event.user.id)
        before = unicode(pre_user)

        if Actions.CHANGE_USERNAME in subscribed_guilds:
            if event.user.username is not UNSET and event.user.username != pre_user.username:
                for guild, config in subscribed_guilds[Actions.CHANGE_USERNAME]:
                    self.log_action_raw(
                        Actions.CHANGE_USERNAME,
                        event,
                        guild,
                        config.plugins.modlog,
                        before=before,
                        after=unicode(event.user))

    @Plugin.listen('MessageUpdate', priority=Priority.BEFORE)
    def on_message_update(self, event):
        if event.author.id == self.state.me.id:
            return

        if event.author.id in event.config.ignored_users:
            return

        if event.channel_id in event.config.ignored_channels:
            return

        try:
            msg = Message.get(Message.id == event.id)
        except Message.DoesNotExist:
            return

        if not event.channel or not event.author:
            return

        if event.content is not UNSET and msg.content != event.with_proper_mentions:
            self.log_action(
                Actions.MESSAGE_EDIT,
                event,
                before=filter_urls(msg.content),
                after=filter_urls(event.with_proper_mentions))

    @Plugin.listen('MessageDelete')
    def on_message_delete(self, event):
        if event.guild.id in self.hushed:
            return

        try:
            msg = Message.get(Message.id == event.id)
        except Message.DoesNotExist:
            return

        channel = self.state.channels.get(msg.channel_id)
        if not channel or not msg.author:
            return

        debounce = self.get_debounce(event.guild.id, msg.author.id, 'censor')
        if debounce:
            self.delete_debounce(event.guild.id, msg.author.id, 'censor')
            return

        if msg.author.id == self.state.me.id:
            return

        if msg.author.id in event.config.ignored_users:
            return

        if msg.channel_id in event.config.ignored_channels:
            return

        self.log_action(Actions.MESSAGE_DELETE, event,
                author=msg.author,
                author_id=msg.author.id,
                channel=channel,
                msg=filter_urls(msg.content),
                attachments='' if not msg.attachments else u'({})'.format(
                    ', '.join(u'<{}>'.format(i) for i in msg.attachments)))

    @Plugin.listen('MessageDeleteBulk')
    def on_message_delete_bulk(self, event):
        channel = self.state.channels.get(event.channel_id)
        if not channel:
            return

        if event.guild.id in self.hushed:
            return

        archive = MessageArchive.create_from_message_ids(event.ids)
        self.log_action(Actions.MESSAGE_DELETE_BULK, event, log=archive.url, channel=channel, count=len(event.ids))

    @Plugin.listen('VoiceStateUpdate', priority=Priority.BEFORE)
    def on_voice_state_update(self, event):
        old_vs = self.state.voice_states.get(event.session_id)

        # Moving channels
        if old_vs and event.channel_id:
            if old_vs.channel_id != event.channel_id:
                self.log_action(
                    Actions.VOICE_CHANNEL_MOVE,
                    event,
                    before_channel=old_vs.channel)
        elif not event.channel_id:
            self.log_action(
                Actions.VOICE_CHANNEL_LEAVE,
                event,
                channel=old_vs.channel)
        elif not old_vs:
            self.log_action(
                Actions.VOICE_CHANNEL_JOIN,
                event)
