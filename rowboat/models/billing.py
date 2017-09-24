import requests

from peewee import (
    BigIntegerField, TextField, DateTimeField
)
from datetime import datetime

from rowboat.config import fastspring
from rowboat.sql import BaseModel
from rowboat.models.guild import Guild


@BaseModel.register
class Subscription(BaseModel):
    sub_id = TextField(primary_key=True)
    user_id = BigIntegerField()
    guild_id = BigIntegerField()

    cancel_reason = TextField(null=True)
    created_at = DateTimeField(default=datetime.utcnow)
    cancelled_at = DateTimeField(null=True)

    class Meta:
        db_table = 'subscriptions'

    @classmethod
    def activate(cls, sub_id, user_id, guild_id):
        sub = Subscription.create(
            sub_id=sub_id,
            user_id=user_id,
            guild_id=guild_id,
        )

        try:
            guild = Guild.with_id(guild_id)
        except Guild.DoesNotExist:
            sub.cancel('automatic - invalid guild id')
            return

        if guild.premium_sub_id is not None:
            sub.cancel('automatic - guild already has premium %s' % guild.premium_sub_id)
            return

        guild.premium_sub_id = sub.sub_id
        guild.save()

    def cancel(self, reason, force=True):
        if force:
            r = requests.delete(
                'https://api.fastspring.com/subscriptions/{}'.format(self.sub_id),
                auth=(fastspring['username'], fastspring['password']),
            )
            r.raise_for_status()

        Guild.update(premium_sub_id=None).where(
            (Guild.guild_id == self.guild_id)
        ).execute()

        Subscription.update(
            cancel_reason=reason,
            cancelled_at=datetime.utcnow()
        ).where(
            (Subscription.sub_id == sub_id)
        ).execute()

        Guild.with_id(self.guild_id).emit_update()

    def serialize(self, user=None, guild=None):
        return {
            'id': self.sub_id,
            'user': user or {'id': self.user_id},
            'guild': guild or {'id': self.guild_id},
            'created_at': self.created_at,
        }
