from datetime import datetime
from peewee import (
    BigIntegerField, SmallIntegerField, BooleanField, TextField, DateTimeField,
)
from rowboat.sql import BaseModel


@BaseModel.register
class User(BaseModel):
    user_id = BigIntegerField(primary_key=True)
    username = TextField()
    discriminator = SmallIntegerField()
    avatar = TextField()
    bot = BooleanField()

    created_at = DateTimeField(default=datetime.utcnow)

    class Meta:
        db_table = 'users'

        indexes = (
            (('id', 'username', 'discriminator'), True),
        )

    @classmethod
    def from_disco_user(cls, user, should_update=True):
        obj, _ = cls.get_or_create(
            user_id=user.id,
            defaults={
                'username': user.username,
                'discriminator': user.discriminator,
                'avatar': user.avatar,
                'bot': user.bot
            })

        if should_update:
            updates = {}

            if obj.username != user.username:
                updates['username'] = user.username

            if obj.discriminator != user.discriminator:
                updates['discriminator'] = user.discriminator

            if obj.avatar != user.avatar:
                updates['avatar'] = user.avatar

            if updates:
                cls.update(**updates).where(User.user_id == user.id).execute()

        return obj

    def __str__(self):
        return u'{}#{}'.format(self.username, str(self.discriminator).zfill(4))
