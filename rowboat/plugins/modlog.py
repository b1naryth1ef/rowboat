import six
import time
import pytz
import requests
import operator

from six import BytesIO
from PIL import Image
from holster.enum import Enum
from holster.emitter import Priority
from datetime import datetime

from disco.types import UNSET
from disco.types.message import MessageEmbed, MessageEmbedField
from disco.util.functional import cached_property
from disco.util.snowflake import to_unix

from rowboat import RowboatPlugin as Plugin
from rowboat.types import SlottedModel, Field, ListField, DictField, ChannelField
from rowboat.types.plugin import PluginConfig
from rowboat.plugins.messages import Message
from rowboat.util import ordered_load

ZERO_WIDTH_SPACE = u'\u200B'

Actions = Enum()

COLORS = {
    'red': 0xff0033,
    'orange': 0xff7700,
    'green': 0x009a44,
}


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
    channels = DictField(ChannelField, ChannelConfig)
    new_member_threshold = Field(int, default=(15 * 60))

    @cached_property
    def subscribed(self):
        return reduce(operator.or_, (i.subscribed for i in self.channels.values()))


class ModLogPlugin(Plugin):
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

        self.debounce = ctx.get('debounce', {})

        super(ModLogPlugin, self).load(ctx)

    def unload(self, ctx):
        ctx['action_rich'] = self.action_rich
        ctx['action_simple'] = self.action_simple
        ctx['debounce'] = self.debounce
        super(ModLogPlugin, self).unload(ctx)

    def log_action(self, action, event, attachment=None, **details):
        if not {action} & event.config.subscribed:
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

                field.value = v.format(e=event, **details).replace('@', '@' + ZERO_WIDTH_SPACE)
                embed.fields.append(field)

            return '', embed

        def generate_simple(config):
            info = self.action_simple.get(action)

            msg = ':{}: {}'.format(
                info['emoji'],
                info['format'].format(e=event, **details).replace('@', '@' + ZERO_WIDTH_SPACE))

            if config.timestamps:
                ts = pytz.utc.localize(datetime.utcnow()).astimezone(config.tz)
                msg = '`[{}]` '.format(ts.strftime('%H:%M:%S')) + msg

            return msg, None

        for channel, config in event.config.channels.items():
            if not {action} & config.subscribed:
                continue

            # TODO: consider caching this better
            if isinstance(channel, int):
                cobj = event.guild.channels.get(channel)
            else:
                cobj = event.guild.channels.select_one(name=channel)

            if not cobj:
                self.log.warning('Invalid channel: %s', channel)
                continue

            if config.compact and action not in config.rich:
                msg, embed = generate_simple(config)
            else:
                msg, embed = generate_rich(config)

            cobj.send_message(msg, embed=embed, attachment=attachment if config.compact else None)

    @Plugin.schedule(120)
    def cleanup_debounce(self):
        for k, v in list(six.iteritems(self.debounce)):
            if v + 120 > time.time():
                del self.debounce[k]

    @Plugin.listen('ChannelCreate')
    def on_channel_create(self, event):
        self.log_action(Actions.CHANNEL_CREATE, event)

    @Plugin.listen('ChannelDelete')
    def on_channel_delete(self, event):
        self.log_action(Actions.CHANNEL_DELETE, event)

    @Plugin.listen('GuildBanAdd')
    def on_guild_ban_add(self, event):
        self.debounce[event.user.id] = time.time()
        self.log_action(Actions.GUILD_BAN_ADD, event)

    @Plugin.listen('GuildBanRemove')
    def on_guild_ban_remove(self, event):
        self.log_action(Actions.GUILD_BAN_REMOVE, event)

    @Plugin.listen('GuildMemberAdd')
    def on_guild_member_add(self, event):
        if event.user.id in self.debounce:
            del self.debounce[event.user.id]

        new = (time.time() - to_unix(event.user.id) < event.config.new_member_threshold)
        self.log_action(Actions.GUILD_MEMBER_ADD, event, new=':new:' if new else '')

    @Plugin.listen('GuildMemberRemove')
    def on_guild_member_remove(self, event):
        if event.user.id in self.debounce:
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

        if pre_member.nick != event.nick:
            self.log_action(
                Actions.CHANGE_NICK,
                event,
                before=pre_member.nick or '<NO_NICK>',
                after=event.nick or '<NO_NICK>')

        if pre_member.user.username != event.user.username:
            self.log_action(
                Actions.CHANGE_USERNAME,
                event,
                before=pre_member.user.username,
                after=event.user.username)

        pre_roles = set(pre_member.roles)
        post_roles = set(event.roles)
        if pre_roles != post_roles:
            added = post_roles - pre_roles
            removed = pre_roles - post_roles

            for role in filter(bool, map(event.guild.roles.get, added)):
                self.log_action(Actions.GUILD_MEMBER_ROLES_ADD, event, role=role)

            for role in filter(bool, map(event.guild.roles.get, removed)):
                self.log_action(Actions.GUILD_MEMBER_ROLES_RMV, event, role=role)

    @Plugin.listen('PresenceUpdate', priority=Priority.BEFORE)
    def on_presence_update(self, event):
        """
        TODO:
            optimize so we only DL images once per guild
        """
        if Actions.GUILD_MEMBER_AVATAR_CHANGE not in event.config.subscribed:
            return

        pre_member = event.guild.members.get(event.user.id)
        if not pre_member:
            return

        if event.user.avatar is UNSET:
            return

        if pre_member.user.avatar != event.user.avatar:
            image = Image.new('RGB', (256, 128))

            if pre_member.user.avatar:
                r = requests.get(pre_member.user.avatar_url)
                image.paste(Image.open(BytesIO(r.content)), (0, 0))

            if event.user.avatar:
                r = requests.get(event.user.avatar_url)
                image.paste(Image.open(BytesIO(r.content)), (128, 0))

            combined = BytesIO()
            image.save(combined, 'jpeg', quality=55)
            combined.seek(0)
            self.log_action(Actions.GUILD_MEMBER_AVATAR_CHANGE, event, attachment=('avatar.jpg', combined))

    @Plugin.listen('MessageUpdate', priority=Priority.BEFORE)
    def on_message_update(self, event):
        try:
            msg = Message.get(Message.id == event.id)
        except Message.DoesNotExist:
            return

        if not event.channel or not event.author:
            return

        if msg.content != event.content:
            self.log_action(Actions.MESSAGE_EDIT, event, before=msg.content, after=event.content)

    @Plugin.listen('MessageDelete')
    def on_message_delete(self, event):
        try:
            msg = Message.get(Message.id == event.id)
        except Message.DoesNotExist:
            return

        channel = self.state.channels.get(msg.channel_id)
        if not channel or not msg.author:
            return

        self.log_action(Actions.MESSAGE_DELETE, event,
                author=msg.author,
                author_id=msg.author.id,
                channel=channel,
                msg=msg.content)
