from disco.bot import Plugin
from peewee import (
    BigIntegerField, TextField, SmallIntegerField, BooleanField,
    ForeignKeyField, DateTimeField
)

from rowboat.sql import BaseModel


@BaseModel.register
class MessageAuthor(BaseModel):
    id = BigIntegerField(primary_key=True)
    username = TextField()
    discriminator = SmallIntegerField()
    bot = BooleanField()

    class Meta:
        indexes = (
            (('username', 'discriminator'), True),
        )

    def __str__(self):
        return u'{}#{}'.format(self.username, self.discriminator)


@BaseModel.register
class Message(BaseModel):
    id = BigIntegerField(primary_key=True)
    channel_id = BigIntegerField(index=True)
    author = ForeignKeyField(MessageAuthor)
    content = TextField()
    timestamp = DateTimeField()
    edited_timestamp = DateTimeField(null=True, default=None)
    deleted = BooleanField(default=False)

    SQL = '''CREATE INDEX IF NOT EXISTS message_content_fts ON message USING gin(to_tsvector('english', content));'''


class MessageCachePlugin(Plugin):
    @Plugin.listen('MessageCreate')
    def on_message_create(self, event):
        author, _ = MessageAuthor.get_or_create(
            id=event.author.id,
            defaults={
                'username': event.author.username,
                'discriminator': event.author.discriminator,
                'bot': event.author.bot,
            })

        Message.create(
            id=event.id,
            channel_id=event.channel_id,
            author=author,
            content=event.content,
            timestamp=event.timestamp)

    @Plugin.listen('MessageUpdate')
    def on_message_update(self, event):
        to_update = {
            'edited_timestamp': event.edited_timestamp
        }

        if event.content:
            to_update['content'] = event.content

        Message.update(**to_update).where(Message.id == event.id).execute()

    @Plugin.listen('MessageDelete')
    def on_message_delete(self, event):
        Message.update(deleted=True).where(Message.id == event.id).execute()
