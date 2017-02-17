import re
import six
import time
import pytz
import requests
import operator
import humanize

from six import BytesIO
from PIL import Image
from holster.enum import Enum
from holster.emitter import Priority
from datetime import datetime
from collections import defaultdict

from disco.types import UNSET
from disco.types.message import MessageEmbed, MessageEmbedField
from disco.util.functional import cached_property
from disco.util.snowflake import to_unix, to_datetime

from rowboat import RowboatPlugin as Plugin
from rowboat.types import SlottedModel, Field, ListField, DictField, ChannelField
from rowboat.types.plugin import PluginConfig
from rowboat.models.message import Message
from rowboat.util import ordered_load, C


Actions = Enum()

COLORS = {
    'red': 0xff0033,
    'orange': 0xff7700,
    'green': 0x009a44,
}

URL_REGEX = re.compile(r'(https?://[^\s]+)')


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


class ModLogConfig(PluginConfig):
    resolved = Field(bool, default=False, private=True)

    ignored_users = ListField(Actions)

    channels = DictField(ChannelField, ChannelConfig)
    new_member_threshold = Field(int, default=(15 * 60))

    @cached_property
    def subscribed(self):
        return reduce(operator.or_, (i.subscribed for i in self.channels.values())) if self.channels else set()


class ModLogPlugin(Plugin):
    def create_debounce(self, event, user, typ, **kwargs):
        kwargs.update({
            'type': typ,
            'time': time.time(),
        })
        # TODO: check if we should even track this

        self.debounce[event.guild.id][user.id] = kwargs

    def resolve_channels(self, guild, config):
        new_channels = {}

        for key, channel in config.channels.items():
            if isinstance(key, int):
                chan = guild.channels.select_one(id=key)
            else:
                chan = guild.channels.select_one(name=key)

            if chan:
                new_channels[chan.id] = channel

        config.channels = new_channels

    def register_action(self, name, rich, simple):
        action = Actions.add(name)
        self.action_rich[action] = rich
        self.action_simple[action] = simple

    def load(self, ctx):
        if not Actions.attrs:
            self.action_rich = {}
            self.action_simple = {}

            with open('data/actions_rich.yaml') as f:
                rich = ordered_load(f.read())

            with open('data/actions_simple.yaml') as f:
                simple = ordered_load(f.read())

            for k, v in rich.items():
                self.register_action(k, v, simple[k])
        else:
            self.action_rich = ctx['action_rich']
            self.action_simple = ctx['action_simple']

        self.debounce = ctx.get('debounce', defaultdict(dict))

        super(ModLogPlugin, self).load(ctx)

    def unload(self, ctx):
        ctx['action_rich'] = self.action_rich
        ctx['action_simple'] = self.action_simple
        ctx['debounce'] = self.debounce
        super(ModLogPlugin, self).unload(ctx)

    def log_action_ext(self, action, event, attachment=None, **details):
        assert hasattr(event.base_config.plugins, 'modlog')
        return self.log_action_raw(action, event, event.guild, getattr(event.base_config.plugins, 'modlog'), attachment, **details)

    def log_action(self, action, event, attachment=None, **details):
        return self.log_action_raw(action, event, event.guild, event.config, attachment, **details)

    def log_action_raw(self, action, event, guild, config, attachment=None, **details):
        if not config.resolved:
            self.resolve_channels(guild, config)
            config.resolved = True

        if not {action} & config.subscribed:
            return

        def generate_rich(config):
            info = self.action_rich.get(action)

            embed = MessageEmbed()
            embed.color = COLORS[info['color']]
            embed.description = info['text'].title()

            for k, v in info['fields'].items():
                field = MessageEmbedField()
                field.name = k.title()

                field.inline = True
                if field.name.endswith('!'):
                    field.inline = False
                    field.name = field.name[:-1]

                if '-' in field.name:
                    field.name = field.name.replace('-', ' ').title()

                field.value = C(v.format(e=event, **details))
                embed.fields.append(field)

            return '', embed

        def generate_simple(config):
            info = self.action_simple.get(action)

            msg = u':{}: {}'.format(
                info['emoji'],
                C(six.text_type(info['format']).format(e=event, **details)))

            if config.timestamps:
                ts = pytz.utc.localize(datetime.utcnow()).astimezone(config.tz)
                msg = '`[{}]` '.format(ts.strftime('%H:%M:%S')) + msg

            if len(msg) > 2000:
                msg = msg[0:1997] + '...'

            return msg, None

        for channel, config in config.channels.items():
            if not {action} & config.subscribed:
                continue

            if config.compact and action not in config.rich:
                msg, embed = generate_simple(config)
            else:
                msg, embed = generate_rich(config)

            channel = guild.channels.get(channel)
            if channel:
                channel.send_message(msg, embed=embed, attachment=attachment if config.compact else None)

    @Plugin.schedule(120)
    def cleanup_debounce(self):
        for obj in six.itervalues(self.debounce):
            for k, v in list(six.iteritems(obj)):
                if v['time'] + 30 > time.time():
                    del obj[k]

    @Plugin.listen('ChannelCreate')
    def on_channel_create(self, event):
        self.log_action(Actions.CHANNEL_CREATE, event)

    @Plugin.listen('ChannelDelete')
    def on_channel_delete(self, event):
        self.log_action(Actions.CHANNEL_DELETE, event)

    @Plugin.listen('GuildBanAdd')
    def on_guild_ban_add(self, event):
        if event.user.id in self.debounce[event.guild.id]:
            debounce = self.debounce[event.guild.id][event.user.id]

            if debounce['type'] == 'ban_reason':
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

        self.create_debounce(event, event.user, 'ban')

    @Plugin.listen('GuildBanRemove')
    def on_guild_ban_remove(self, event):
        if event.user.id in self.debounce[event.guild_id]:
            debounce = self.debounce[event.guild_id][event.user.id]

            # Ignore softbans
            if debounce['type'] == 'ban_reason':
                if debounce['temp'] and not debounce['expires']:
                    return

        self.log_action(Actions.GUILD_BAN_REMOVE, event)

    @Plugin.listen('GuildMemberAdd')
    def on_guild_member_add(self, event):
        if event.user.id in self.debounce[event.guild.id]:
            del self.debounce[event.guild.id][event.user.id]

        new = ''
        if event.config.new_member_threshold and (time.time() - to_unix(event.user.id)) < event.config.new_member_threshold:
            new = ' :new: (created {})'.format(humanize.naturaltime(datetime.utcnow() - to_datetime(event.user.id)))

        self.log_action(Actions.GUILD_MEMBER_ADD, event, new=' :new:' if new else '')

    @Plugin.listen('GuildMemberRemove')
    def on_guild_member_remove(self, event):
        if event.user.id in self.debounce[event.guild.id]:
            debounce = self.debounce[event.guild.id][event.user.id]

            if debounce['type'] == 'kick':
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

        if pre_member.nick != event.nick:
            if not pre_member.nick:
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

            for role in filter(bool, map(event.guild.roles.get, added)):
                self.log_action(Actions.GUILD_MEMBER_ROLES_ADD, event, role=role)

            for role in filter(bool, map(event.guild.roles.get, removed)):
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
            if not config.plugins.modlog:
                continue

            if event.user.id in config.plugins.modlog.ignored_users:
                continue

            for act in {Actions.CHANGE_USERNAME, Actions.GUILD_MEMBER_AVATAR_CHANGE}:
                if {act} & config.plugins.modlog.subscribed:
                    subscribed_guilds[act].append((guild, config))

        if not len(subscribed_guilds):
            return

        pre_user = self.state.users.get(event.user.id)

        if Actions.CHANGE_USERNAME in subscribed_guilds:
            if event.user.username is not UNSET and event.user.username != pre_user.username:
                for guild, config in subscribed_guilds[Actions.CHANGE_USERNAME]:
                    self.log_action_raw(
                        Actions.CHANGE_USERNAME,
                        event,
                        guild,
                        config.plugins.modlog,
                        before=pre_user.username,
                        after=event.user.username)

        if Actions.GUILD_MEMBER_AVATAR_CHANGE in subscribed_guilds:
            if event.user.avatar is not UNSET and event.user.avatar != pre_user.avatar:

                image = Image.new('RGB', (256, 128))

                if pre_user.avatar:
                    r = requests.get(pre_user.avatar_url)
                    image.paste(Image.open(BytesIO(r.content)), (0, 0))

                if event.user.avatar:
                    r = requests.get(event.user.avatar_url)
                    image.paste(Image.open(BytesIO(r.content)), (128, 0))

                combined = BytesIO()
                image.save(combined, 'jpeg', quality=55)
                combined.seek(0)

                for guild, config in subscribed_guilds[Actions.GUILD_MEMBER_AVATAR_CHANGE]:
                    self.log_action_raw(
                        Actions.GUILD_MEMBER_AVATAR_CHANGE,
                        event,
                        guild,
                        config.plugins.modlog,
                        attachment=('avatar.jpg', combined))

    @Plugin.listen('MessageUpdate', priority=Priority.BEFORE)
    def on_message_update(self, event):
        if event.author.id == self.state.me.id:
            return

        if event.author.id in event.config.ignored_users:
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
        try:
            msg = Message.get(Message.id == event.id)
        except Message.DoesNotExist:
            return

        channel = self.state.channels.get(msg.channel_id)
        if not channel or not msg.author:
            return

        if msg.author.id == self.state.me.id:
            return

        if event.author.id in event.config.ignored_users:
            return

        self.log_action(Actions.MESSAGE_DELETE, event,
                author=msg.author,
                author_id=msg.author.id,
                channel=channel,
                msg=filter_urls(msg.content))

    @Plugin.listen('MessageDeleteBulk')
    def on_message_delete_bulk(self, event):
        channel = self.state.channels.get(event.channel_id)
        if not channel:
            return

        msgs = Message.select(
            Message.id,
            Message.timestamp,
            Message.author,
            Message.content,
            Message.deleted
        ).where(
            Message.id << event.ids
        ).order_by(Message.id.desc())

        try:
            url = self.bot.plugins.get('AdminPlugin').archive_messages(msgs, 'txt')
        except:
            self.log.exception('Failed to dump message log for bulk delete: ')
            url = ''

        # TODO: upload messages in txt file
        self.log_action(Actions.MESSAGE_DELETE_BULK, event, log=url, channel=channel, count=len(event.ids))
