from peewee import (BigIntegerField, SmallIntegerField, CharField, TextField, BooleanField)

from rowboat.sql import BaseModel
from rowboat.models.message import Message


@BaseModel.register
class Channel(BaseModel):
    channel_id = BigIntegerField(primary_key=True)
    guild_id = BigIntegerField(null=True)
    name = CharField(null=True, index=True)
    topic = TextField(null=True)
    type_ = SmallIntegerField(null=True)

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
        # Upsert channel information
        channel = list(cls.insert(
            channel_id=channel.id,
            guild_id=channel.guild.id if channel.guild else None,
            name=channel.name or None,
            topic=channel.topic or None,
            type_=channel.type,
        ).upsert(target=cls.channel_id).returning(cls.first_message_id).execute())[0]

        # Update the first message ID
        if not channel.first_message_id:
            cls.update(
                first_message_id=cls.generate_first_message_id(channel.id)
            ).where(cls.channel_id == channel.id).execute()
