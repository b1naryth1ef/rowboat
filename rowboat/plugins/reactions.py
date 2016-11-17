from emoji.unicode_codes import EMOJI_ALIAS_UNICODE
from peewee import fn

from rowboat import RowboatPlugin as Plugin
from rowboat.types import SlottedModel, ListField, DictField, ChannelField, UserField, Field
from rowboat.types.plugin import PluginConfig
from rowboat.plugins.messages import Reaction

REGION_INDICATOR_RANGE = (u'\U0001F1E6', u'\U0001F1FF')


def emoji(data):
    if not data.startswith(':'):
        data = ':' + data

    if not data.endswith(':'):
        data += ':'

    if data in EMOJI_ALIAS_UNICODE.values():
        return data

    if data not in EMOJI_ALIAS_UNICODE.keys():
        raise ValueError(u'Unknown emoji {}'.format(data))

    return EMOJI_ALIAS_UNICODE[data]


class BaseConfig(SlottedModel):
    whitelist = ListField(emoji)
    blacklist = ListField(emoji)

    no_letters = Field(bool, default=False)


class ChannelConfig(BaseConfig):
    max_reactions_per_user = Field(int)
    max_reactions_emojis = Field(int)
    max_reactions = Field(int)


class ReactionsConfig(PluginConfig):
    whitelisted = True

    resolved = Field(bool, default=False, private=True)

    channels = DictField(ChannelField, ChannelConfig)
    users = DictField(UserField, BaseConfig)


class ReactionsPlugin(Plugin):
    def resolve_channels(self, event):
        new_channels = {}

        for key, channel in event.config.channels.items():
            if key == '*':
                new_channels[key] = channel
                continue

            if isinstance(key, int):
                chan = event.guild.channels.select_one(id=key)
            else:
                chan = event.guild.channels.select_one(name=key)

            if chan:
                new_channels[chan.id] = channel

        event.config.channels = new_channels

    @Plugin.listen('MessageReactionAdd')
    def on_message_reaction_add(self, event):
        if not event.config.resolved:
            self.resolve_channels(event)
            event.config.resolved = True

        configs = []
        if event.channel_id in event.config.channels:
            configs.append(event.config.channels[event.channel_id])
        elif '*' in event.config.channels:
            configs.append(event.config.channels['*'])

        if event.user_id in event.config.users:
            configs.append(event.config.users[event.user_id])
        elif '*' in event.config.users:
            configs.append(event.config.users['*'])

        if not self.on_update(event, configs):
            self.revert(event)

    def revert(self, event):
        try:
            event.client.api.channels_messages_reactions_delete(
                event.channel_id,
                event.message_id,
                event.emoji.to_string(),
                event.user_id
            )
        except:
            self.log.exception('Failed to revert reaction: ')

    def on_update(self, event, configs):
        for cfg in configs:
            if cfg.whitelist:
                if not any(map(lambda k: event.emoji.name.startswith(k), cfg.whitelist)):
                    return False

            if cfg.blacklist:
                if any(map(lambda k: event.emoji.name.startswith(k), cfg.blacklist)):
                    return False

            if cfg.no_letters:
                if len(event.emoji.name) == 1:
                    start, end = REGION_INDICATOR_RANGE
                    if start <= event.emoji.name[0] <= end:
                        return False

            if isinstance(cfg, ChannelConfig):
                if cfg.max_reactions is not None:
                    reaction_count = Reaction.select().where(
                        (Reaction.message_id == event.message_id)
                    ).count()

                    if reaction_count > cfg.max_reactions:
                        return False

                if cfg.max_reactions_emojis is not None:
                    reaction_count = len(Reaction.select(
                        Reaction.emoji_name, fn.COUNT(Reaction.id)
                    ).where(
                        (Reaction.message_id == event.message_id)
                    ).group_by(Reaction.emoji_name).execute())

                    if reaction_count > cfg.max_reactions_emojis:
                        return False

                if cfg.max_reactions_per_user is not None:
                    reaction_count = Reaction.select().where(
                        (Reaction.message_id == event.message_id) &
                        (Reaction.user_id == event.user_id)
                    ).count()

                    if reaction_count > cfg.max_reactions_per_user:
                        return False

        return True
