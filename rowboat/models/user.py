from datetime import datetime, timedelta
from holster.enum import Enum
from peewee import BigIntegerField, IntegerField, SmallIntegerField, TextField, BooleanField, DateTimeField
from rowboat.sql import BaseModel


@BaseModel.register
class User(BaseModel):
    user_id = BigIntegerField(primary_key=True)
    username = TextField()
    discriminator = SmallIntegerField()
    avatar = TextField(null=True)
    bot = BooleanField()

    created_at = DateTimeField(default=datetime.utcnow)

    SQL = '''
        CREATE INDEX IF NOT EXISTS users_username_trgm ON users USING gin(username gin_trgm_ops);
    '''

    class Meta:
        db_table = 'users'

        indexes = (
            (('id', 'username', 'discriminator'), True),
        )

    @property
    def id(self):
        return self.user_id

    @classmethod
    def ensure(cls, user, should_update=True):
        return cls.from_disco_user(user)

    @classmethod
    def from_disco_user(cls, user, should_update=True):
        # DEPRECATED
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
        'MUTE',
        'KICK',
        'TEMPBAN',
        'SOFTBAN',
        'BAN',
        bitmask=False,
    )

    guild_id = BigIntegerField()
    user_id = BigIntegerField()
    actor_id = BigIntegerField(null=True)

    type_ = IntegerField(db_column='type')
    reason = TextField(null=True)

    expires_at = DateTimeField(null=True)
    created_at = DateTimeField(default=datetime.utcnow)
    active = BooleanField(default=True)

    class Meta:
        db_table = 'infractions'

        indexes = (
            (('guild', 'user_id'), False),
        )

    @classmethod
    def kick(cls, plugin, event, member, reason):
        User.from_disco_user(member.user)
        plugin.bot.plugins.get('ModLogPlugin').create_debounce(event, member.user, 'kick',
            actor=str(event.author),
            reason=reason or 'no reason')
        member.kick()
        cls.create(
            guild_id=member.guild_id,
            user_id=member.user.id,
            actor_id=event.author.id,
            type_=cls.Types.KICK,
            reason=reason)

    @classmethod
    def tempban(cls, plugin, event, member, reason, duration):
        User.from_disco_user(member.user)
        expires_at = datetime.utcnow() + timedelta(seconds=duration)

        plugin.bot.plugins.get('ModLogPlugin').create_debounce(event, member.user, 'ban_reason',
            actor=str(event.author),
            temp=True,
            expires=expires_at,
            reason=reason or 'no reason')

        member.ban()

        cls.create(
            guild_id=member.guild_id,
            user_id=member.user.id,
            actor_id=event.author.id,
            type_=cls.Types.TEMPBAN,
            reason=reason,
            expires_at=expires_at)

    @classmethod
    def softban(cls, plugin, event, member, reason):
        User.from_disco_user(member.user)
        plugin.bot.plugins.get('ModLogPlugin').create_debounce(event, member.user, 'ban_reason',
            actor=str(event.author),
            temp=True,
            expires=None,
            reason=reason or 'no reason')

        member.ban(delete_message_days=7)
        member.unban()
        cls.create(
            guild_id=member.guild_id,
            user_id=member.user.id,
            actor_id=event.author.id,
            type_=cls.Types.SOFTBAN,
            reason=reason)

    @classmethod
    def ban(cls, plugin, event, member, reason, guild):
        if isinstance(member, (int, long)):
            user_id = member
        else:
            User.from_disco_user(member.user)
            user_id = member.user.id

        if user_id != member:
            plugin.bot.plugins.get('ModLogPlugin').create_debounce(event, member.user, 'ban_reason',
                actor=str(event.author),
                temp=False,
                expires=None,
                reason=reason or 'no reason')

        guild.create_ban(user_id)

        cls.create(
            guild_id=guild.id,
            user_id=user_id,
            actor_id=event.author.id,
            type_=cls.Types.BAN,
            reason=reason)
