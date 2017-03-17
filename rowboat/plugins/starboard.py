from rowboat.plugins import RowboatPlugin as Plugin
from rowboat.types.plugin import PluginConfig
from rowboat.types import ChannelField, Field
from rowboat.models.message import StarboardEntry
from rowboat.util.timing import Debounce


STAR_EMOJI = u'\U00002B50'


def is_star_event(e):
    if e.emoji.name == STAR_EMOJI:
        return True


class StarboardConfig(PluginConfig):
    channel = Field(ChannelField)


@Plugin.with_config(StarboardConfig)
class StarboardPlugin(Plugin):
    def load(self, ctx):
        super(StarboardPlugin, self).load(ctx)
        self.updates = {}

    def queue_update(self, guild_id, config):
        if guild_id in self.updates:
            self.updates[guild_id].touch()
        else:
            self.updates[guild_id] = Debounce(self.update_starboard, 5, 10, guild_id=guild_id, config=config)

    def update_starboard(self, guild_id, config):
        self.log.info('would update starboard %s / %s', guild_id, config)

    @Plugin.listen('MessageReactionAdd', conditional=is_star_event)
    def on_message_reaction_add(self, event):
        StarboardEntry.add_star(event.message_id, event.user_id)
        self.queue_update(event.guild.id, event.config)

    @Plugin.listen('MessageReactionRemove', conditional=is_star_event)
    def on_message_reaction_remove(self, event):
        StarboardEntry.remove_star(event.message_id, event.user_id)
        self.queue_update(event.guild.id, event.config)


# multiple starboards, constrain source
# Pin top stars
