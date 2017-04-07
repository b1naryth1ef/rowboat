from rowboat.models.migrations import Migrate
from rowboat.models.message import Message


@Migrate.only_if(Migrate.missing, Message, 'mentions_new')
def add_guild_columns(m):
    m.add_columns(
        Message,
        Message.mentions_new,
        Message.emojis_new,
        Message.attachments_new,
        Message.embeds,
    )
