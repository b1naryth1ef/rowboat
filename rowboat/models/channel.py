from peewee import (BigIntegerField, CharField, TextField, BooleanField)

from rowboat.sql import BaseModel
from rowboat.models.message import Message


@BaseModel.register
class Channel(BaseModel):
    channel_id = BigIntegerField(primary_key=True)
    guild_id = BigIntegerField(null=True)
    name = CharField(null=True, index=True)
    topic = TextField(null=True)

    # First message sent in the channel
    first_message_id = BigIntegerField(null=True)
    deleted = BooleanField(default=False)

    class Meta:
        db_table = 'channels'

    @classmethod
    def generate_first_message_id(cls, channel_id):
        try:
            return Message.select(Message.id).where(
                (Message.channel_id == channel_id)
            ).order_by(Message.id.asc()).limit(1).get().id
        except Message.DoesNotExist:
            return None

    @classmethod
    def from_disco_channel(cls, channel):
        try:
            new = False
            obj = cls.get(channel_id=channel.id)
        except cls.DoesNotExist:
            new = True
            obj = cls(channel_id=channel.id)

        obj.guild_id = channel.guild_id or None
        obj.name = channel.name or None
        obj.topic = channel.topic or None

        if new or not obj.first_message_id:
            obj.first_message_id = cls.generate_first_message_id(channel.id)

        obj.save(force_insert=new)
        return obj
