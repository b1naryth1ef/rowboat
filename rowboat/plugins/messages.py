from holster.emitter import Priority
from disco.bot import Plugin
from disco.types.message import MessageTable
from peewee import (
    BigIntegerField, TextField, SmallIntegerField, BooleanField,
    ForeignKeyField, DateTimeField
)

from rowboat.sql import BaseModel, database


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
    guild_id = BigIntegerField(index=True, null=True)
    author = ForeignKeyField(MessageAuthor)
    content = TextField()
    timestamp = DateTimeField()
    edited_timestamp = DateTimeField(null=True, default=None)
    deleted = BooleanField(default=False)

    SQL = '''CREATE INDEX IF NOT EXISTS message_content_fts ON message USING gin(to_tsvector('english', content));'''


@BaseModel.register
class Reaction(BaseModel):
    message_id = BigIntegerField()
    user_id = BigIntegerField()
    emoji_id = BigIntegerField(null=True)
    emoji_name = TextField()


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
            guild_id=(event.guild and event.guild.id),
            author=author,
            content=event.with_proper_mentions,
            timestamp=event.timestamp)

    @Plugin.listen('MessageUpdate')
    def on_message_update(self, event):
        if not event.edited_timestamp:
            return

        to_update = {
            'edited_timestamp': event.edited_timestamp
        }

        if event.content:
            to_update['content'] = event.with_proper_mentions

        Message.update(**to_update).where(Message.id == event.id).execute()

    @Plugin.listen('MessageDelete')
    def on_message_delete(self, event):
        Message.update(deleted=True).where(Message.id == event.id).execute()

    @Plugin.listen('MessageDeleteBulk')
    def on_message_delete_bulk(self, event):
        Message.update(deleted=True).where(
            Message.id << event.ids
        ).execute()

    @Plugin.listen('MessageReactionAdd', priority=Priority.BEFORE)
    def on_message_reaction_add(self, event):
        Reaction.create(
            message_id=event.message_id,
            user_id=event.user_id,
            emoji_id=event.emoji.id or None,
            emoji_name=event.emoji.name or None)

    @Plugin.listen('MessageReactionRemove', priority=Priority.BEFORE)
    def on_message_reaction_remove(self, event):
        Reaction.delete().where(
            (Reaction.message_id == event.message_id) &
            (Reaction.user_id == event.user_id) &
            (Reaction.emoji_id == (event.emoji.id or None)) &
            (Reaction.emoji_name == (event.emoji.name or None))).execute()

    @Plugin.command('sql', level=-1)
    def command_sql(self, event):
        conn = database.obj.get_conn()

        with conn.cursor() as cur:
            cur.execute(event.codeblock.format(e=event))
            tbl = MessageTable()
            tbl.set_header(*[desc[0] for desc in cur.description])

            for row in cur.fetchall():
                tbl.add(*row)

            event.msg.reply(tbl.compile())
