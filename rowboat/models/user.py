from datetime import datetime
from holster.enum import Enum
from peewee import *
from rowboat.sql import BaseModel

from rowboat.models.guild import Guild


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


@BaseModel.register
class Infraction(BaseModel):
    Types = Enum(
        'KICK',
        'TEMPBAN',
        'SOFTBAN',
        'BAN',
    )

    guild = ForeignKeyField(Guild, related_name='infractions')
    user = ForeignKeyField(User, related_name='infractions')
    actor = ForeignKeyField(User, null=True)

    type_ = IntegerField(db_column='type')
    reason = TextField(null=True)

    expires_at = DateTimeField(null=True)
    created_at = DateTimeField(default=datetime.utcnow)
    active = BooleanField(default=True)

    class Meta:
        db_table = 'infractions'

        indexes = (
            (('guild', 'user'), False),
        )

    @classmethod
    def kick(cls, plugin, event, member, reason):
        cls.create(guild=member.guild_id, user=member.user.id, actor=event.author.id, type_=cls.Types.KICK, reason=reason)
        plugin.bot.plugins.get('ModLogPlugin').create_debounce(event, member.user, 'kick',
            actor=str(event.author),
            reason=reason or 'no reason')
        member.kick()

    @classmethod
    def tempban(cls, plugin, event, member, reason, duration):
        expires_at = datetime.utcnow() + timedelta(seconds=duration)

        cls.create(
            guild=member.guild_id,
            user=member.user.id,
            actor=event.author.id,
            type_=cls.Types.TEMPBAN,
            reason=reason,
            expires_at=expires_at)

        plugin.bot.plugins.get('ModLogPlugin').create_debounce(event, member.user, 'ban_reason',
            actor=str(event.author),
            temp=True,
            expires=expires_at,
            reason=reason or 'no reason')

        member.ban()

    @classmethod
    def softban(cls, plugin, event, member, reason):
        cls.create(guild=member.guild_id, user=member.user.id, actor=event.author.id, type_=cls.Types.SOFTBAN, reason=reason)
        plugin.bot.plugins.get('ModLogPlugin').create_debounce(event, member.user, 'ban_reason',
            actor=str(event.author),
            temp=True,
            expires=None,
            reason=reason or 'no reason')

        member.ban()
        member.unban()

    @classmethod
    def ban(cls, plugin, event, member, reason):
        cls.create(guild=member.guild_id, user=member.user.id, actor=event.author.id, type_=cls.Types.BAN, reason=reason)

        plugin.bot.plugins.get('ModLogPlugin').create_debounce(event, member.user, 'ban_reason',
            actor=str(event.author),
            temp=False,
            expires=None,
            reason=reason or 'no reason')

        member.ban()
