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
    channel_id = BigIntegerField(index=True)
    guild_id = BigIntegerField(index=True, null=True)
    author = ForeignKeyField(User)
    content = TextField()
    timestamp = DateTimeField()
    edited_timestamp = DateTimeField(null=True, default=None)
    deleted = BooleanField(default=False)
    num_edits = BigIntegerField(default=0)

    mentions = BinaryJSONField(default=[], null=True)
    emojis = BinaryJSONField(default=[], null=True)

    SQL = '''CREATE INDEX IF NOT EXISTS message_content_fts ON messages USING gin(to_tsvector('english', content));'''

    class Meta:
        db_table = 'messages'

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
                mentions=list(obj.mentions.keys()),
                emojis=list(map(int, EMOJI_RE.findall(obj.content)))))

        for user in obj.mentions.values():
            User.from_disco_user(user)

        return created


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
