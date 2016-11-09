from emoji.unicode_codes import EMOJI_ALIAS_UNICODE
from peewee import fn

from rowboat import RowboatPlugin as Plugin
from rowboat.types import SlottedModel, ListField, DictField, ChannelField, Field
from rowboat.types.plugin import PluginConfig
from rowboat.plugins.messages import Reaction

REGION_INDICATOR_RANGE = (u'\U0001F1E6', u'\U0001F1FF')


def emoji(data):
    if data in EMOJI_ALIAS_UNICODE.values():
        return data

    if data not in EMOJI_ALIAS_UNICODE.keys():
        raise ValueError('Unknown emoji {}'.format(data))

    return EMOJI_ALIAS_UNICODE[data]


class ChannelConfig(SlottedModel):
    max_reactions_per_user = Field(int)
    max_reactions_emojis = Field(int)
    max_reactions = Field(int)

    whitelist = ListField(emoji)
    blacklist = ListField(emoji)

    no_letters = Field(bool, default=False)


class ReactionsConfig(PluginConfig):
    # TODO: private
    resolved = Field(bool, default=False)

    channels = DictField(ChannelField, ChannelConfig)


class ReactionsPlugin(Plugin):
    def resolve_channels(self, event):
        new_channels = {}

        for key, channel in event.config.channels.items():
            if key == '*':
                new_channels[key] = channel
                continue

            if isinstance(key, int):
                chan = event.guild.channels.select_one(id=key).id
            else:
                chan = event.guild.channels.select_one(name=key)

            if not chan:
                continue

            new_channels[chan.id] = channel

        event.config.channels = new_channels

    @Plugin.listen('MessageReactionAdd')
    def on_message_reaction_add(self, event):
        if not event.config.resolved:
            self.resolve_channels(event)
            event.config.resolved = True

        if not event.config.channels:
            return

        if event.channel_id not in event.config.channels:
            if '*' in event.config.channels:
                obj = event.config.channels['*']
            else:
                return
        else:
            obj = event.config.channels[event.channel_id]

        if not self.on_update(event, obj):
            try:
                self.revert(event)
            except:
                self.log.exception('Failed to revert reaction: ')

    def revert(self, event):
        event.client.api.channels_messages_reactions_delete(
            event.channel_id,
            event.message_id,
            event.emoji.to_string(),
            event.user_id
        )

    def on_update(self, event, config):
        if config.whitelist:
            if not any(map(lambda k: event.emoji.name.startswith(k), config.whitelist)):
                return False

        if config.blacklist:
            if any(map(lambda k: event.emoji.name.startswith(k), config.blacklist)):
                return False

        if config.max_reactions is not None:
            reaction_count = Reaction.select().where(
                (Reaction.message_id == event.message_id)
            ).count()

            if reaction_count > config.max_reactions:
                return False

        if config.max_reactions_emojis is not None:
            reaction_count = len(Reaction.select(
                Reaction.emoji_name, fn.COUNT(Reaction.id)
            ).where(
                (Reaction.message_id == event.message_id)
            ).group_by(Reaction.emoji_name).execute())

            if reaction_count > config.max_reactions_emojis:
                return False

        if config.max_reactions_per_user is not None:
            reaction_count = Reaction.select().where(
                (Reaction.message_id == event.message_id) &
                (Reaction.user_id == event.user_id)
            ).count()

            if reaction_count > config.max_reactions_per_user:
                return False

        if config.no_letters:
            if len(event.emoji.name) == 1:
                start, end = REGION_INDICATOR_RANGE
                if start <= event.emoji.name[0] <= end:
                    return False

        return True
