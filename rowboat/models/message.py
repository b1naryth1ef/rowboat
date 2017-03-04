import re

from peewee import (
    BigIntegerField, ForeignKeyField, TextField, DateTimeField,
    BooleanField
)
from playhouse.postgres_ext import BinaryJSONField
from disco.types.base import UNSET

from rowboat.models.user import User
from rowboat.sql import BaseModel

EMOJI_RE = re.compile(r'<:.+:([0-9]+)>')


@BaseModel.register
class Message(BaseModel):
    id = BigIntegerField(primary_key=True)
    channel_id = BigIntegerField()
    guild_id = BigIntegerField(null=True)
    author = ForeignKeyField(User)
    content = TextField()
    timestamp = DateTimeField()
    edited_timestamp = DateTimeField(null=True, default=None)
    deleted = BooleanField(default=False)
    num_edits = BigIntegerField(default=0)

    mentions = BinaryJSONField(default=[], null=True)
    emojis = BinaryJSONField(default=[], null=True)
    attachments = BinaryJSONField(default=[], null=True)

    SQL = '''
        CREATE INDEX IF NOT EXISTS messages_content_fts ON messages USING gin(to_tsvector('english', content));
        CREATE INDEX IF NOT EXISTS messages_content_trgm ON messages USING gin(content gin_trgm_ops);
    '''

    class Meta:
        db_table = 'messages'

        indexes = (
            (('channel_id', 'id'), True),
            (('guild_id', 'id'), True),
            (('author_id', 'id'), True),
        )

    @classmethod
    def from_disco_message_update(cls, obj):
        if not obj.edited_timestamp:
            return

        to_update = {
            'edited_timestamp': obj.edited_timestamp,
            'num_edits': cls.num_edits + 1,
        }

        if obj.content is not UNSET:
            to_update['content'] = obj.with_proper_mentions
            to_update['emojis'] = list(map(int, EMOJI_RE.findall(obj.content)))

        if obj.attachments is not UNSET:
            to_update['attachments'] = [i.url for i in obj.attachments.values()]

        cls.update(**to_update).where(cls.id == obj.id).execute()

    @classmethod
    def from_disco_message(cls, obj):
        _, created = cls.get_or_create(
            id=obj.id,
            defaults=dict(
                channel_id=obj.channel_id,
                guild_id=(obj.guild and obj.guild.id),
                author=User.from_disco_user(obj.author),
                content=obj.with_proper_mentions,
                timestamp=obj.timestamp,
                edited_timestamp=obj.edited_timestamp,
                num_edits=(0 if not obj.edited_timestamp else 1),
                mentions=list(obj.mentions.keys()),
                emojis=list(map(int, EMOJI_RE.findall(obj.content))),
                attachments=[i.url for i in obj.attachments.values()]))

        for user in obj.mentions.values():
            User.from_disco_user(user)

        return created

    @classmethod
    def from_disco_message_many(cls, objs):
        cls.insert_many([{
            'id': obj.id,
            'channel_id': obj.channel_id,
            'guild_id': (obj.guild and obj.guild.id),
            'author': User.from_disco_user(obj.author),
            'content': obj.with_proper_mentions,
            'timestamp': obj.timestamp,
            'edited_timestamp': obj.edited_timestamp,
            'num_edits': (0 if not obj.edited_timestamp else 1),
            'mentions': list(obj.mentions.keys()),
            'emojis': list(map(int, EMOJI_RE.findall(obj.content))),
            'attachments': [i.url for i in obj.attachments.values()],
        } for obj in objs]).execute()

    @classmethod
    def for_channel(cls, channel):
        return cls.select().where(cls.channel_id == channel.id)


@BaseModel.register
class Reaction(BaseModel):
    message_id = BigIntegerField()
    user_id = BigIntegerField()
    emoji_id = BigIntegerField(null=True)
    emoji_name = TextField()

    class Meta:
        db_table = 'reactions'

    @classmethod
    def from_disco_reaction(cls, obj):
        return cls.create(
            message_id=obj.message_id,
            user_id=obj.user_id,
            emoji_id=obj.emoji.id or None,
            emoji_name=obj.emoji.name or None)
