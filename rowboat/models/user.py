from datetime import datetime
from holster.enum import Enum
from peewee import BigIntegerField, IntegerField, SmallIntegerField, TextField, BooleanField, DateTimeField
from playhouse.postgres_ext import BinaryJSONField
from rowboat.sql import BaseModel


@BaseModel.register
class User(BaseModel):
    user_id = BigIntegerField(primary_key=True)
    username = TextField()
    discriminator = SmallIntegerField()
    avatar = TextField(null=True)
    bot = BooleanField()

    created_at = DateTimeField(default=datetime.utcnow)

    admin = BooleanField(default=False)

    SQL = '''
        CREATE INDEX IF NOT EXISTS users_username_trgm ON users USING gin(username gin_trgm_ops);
    '''

    class Meta:
        db_table = 'users'

        indexes = (
            (('user_id', 'username', 'discriminator'), True),
        )

    @property
    def id(self):
        return self.user_id

    @classmethod
    def ensure(cls, user, should_update=True):
        return cls.from_disco_user(user)

    @classmethod
    def with_id(cls, uid):
        try:
            return User.get(user_id=uid)
        except User.DoesNotExist:
            return

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

    def get_avatar_url(self, fmt='webp', size=1024):
        if not self.avatar:
            return None

        return 'https://cdn.discordapp.com/avatars/{}/{}.{}?size={}'.format(
            self.user_id,
            self.avatar,
            fmt,
            size
        )

    def __unicode__(self):
        return u'{}#{}'.format(self.username, str(self.discriminator).zfill(4))


@BaseModel.register
class Infraction(BaseModel):
    Types = Enum(
        'MUTE',
        'KICK',
        'TEMPBAN',
        'SOFTBAN',
        'BAN',
        'TEMPMUTE',
        'UNBAN',
        'TEMPROLE',
        bitmask=False,
    )

    guild_id = BigIntegerField()
    user_id = BigIntegerField()
    actor_id = BigIntegerField(null=True)

    type_ = IntegerField(db_column='type')
    reason = TextField(null=True)
    metadata = BinaryJSONField(default={})

    expires_at = DateTimeField(null=True)
    created_at = DateTimeField(default=datetime.utcnow)
    active = BooleanField(default=True)

    class Meta:
        db_table = 'infractions'

        indexes = (
            (('guild_id', 'user_id'), False),
        )

    @staticmethod
    def admin_config(event):
        return getattr(event.base_config.plugins, 'admin', None)

    @classmethod
    def temprole(cls, plugin, event, member, role_id, reason, expires_at):
        User.from_disco_user(member.user)

        member.add_role(role_id)

        cls.create(
            guild_id=event.guild.id,
            user_id=member.user.id,
            actor_id=event.author.id,
            type_=cls.Types.TEMPROLE,
            reason=reason,
            expires_at=expires_at,
            metadata={'role': role_id})

    @classmethod
    def kick(cls, plugin, event, member, reason):
        User.from_disco_user(member.user)
        plugin.bot.plugins.get('ModLogPlugin').create_debounce(event, member.user.id, 'kick',
            actor=unicode(event.author) if event.author.id != member.id else 'Automatic',
            reason=reason or 'no reason')
        member.kick()
        cls.create(
            guild_id=member.guild_id,
            user_id=member.user.id,
            actor_id=event.author.id,
            type_=cls.Types.KICK,
            reason=reason)

    @classmethod
    def tempban(cls, plugin, event, member, reason, expires_at):
        User.from_disco_user(member.user)

        plugin.bot.plugins.get('ModLogPlugin').create_debounce(event, member.user.id, 'ban_reason',
            actor=unicode(event.author) if event.author.id != member.id else 'Automatic',
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
        plugin.bot.plugins.get('ModLogPlugin').create_debounce(event, member.user.id, 'ban_reason',
            actor=unicode(event.author) if event.author.id != member.id else 'Automatic',
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

        plugin.bot.plugins.get('ModLogPlugin').create_debounce(event, user_id, 'ban_reason',
            actor=unicode(event.author) if event.author.id != user_id else 'Automatic',
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

    @classmethod
    def mute(cls, plugin, event, member, reason):
        admin_config = cls.admin_config(event)

        plugin.bot.plugins.get('ModLogPlugin').create_debounce(
            event,
            member.user.id,
            'muted',
            reason=reason,
            expires_at=None,
            actor=unicode(event.author) if event.author.id != member.id else 'Automatic',
            role=admin_config.mute_role)

        member.add_role(admin_config.mute_role)
        cls.create(
            guild_id=event.guild.id,
            user_id=member.user.id,
            actor_id=event.author.id,
            type_=cls.Types.MUTE,
            reason=reason,
            metadata={'role': admin_config.mute_role})

    @classmethod
    def tempmute(cls, plugin, event, member, reason, expires_at):
        admin_config = cls.admin_config(event)

        plugin.bot.plugins.get('ModLogPlugin').create_debounce(
            event,
            member.user.id,
            'muted',
            reason=reason,
            expires_at=expires_at,
            actor=unicode(event.author) if event.author.id != member.id else 'Automatic',
            role=admin_config.mute_role)

        member.add_role(admin_config.mute_role)

        cls.create(
            guild_id=event.guild.id,
            user_id=member.user.id,
            actor_id=event.author.id,
            type_=cls.Types.TEMPMUTE,
            reason=reason,
            expires_at=expires_at,
            metadata={'role': admin_config.mute_role})

    @classmethod
    def clear_active(cls, event, user_id, types):
        """
        Marks a previously active tempmute as inactive for the given event/user.
        This should be used in all locations where we either think this is no
        longer active (e.g. the mute role was removed) _or_ when we don't want to
        unmute the user any longer, e.g. they've been remuted by another command.
        """
        return cls.update(active=False).where(
            (cls.guild_id == event.guild.id) &
            (cls.user_id == user_id) &
            (cls.type_ << types) &
            (cls.active == 1)
        ).execute() >= 1


@BaseModel.register
class StarboardBlock(BaseModel):
    guild_id = BigIntegerField()
    user_id = BigIntegerField()
    actor_id = BigIntegerField()

    class Meta:
        indexes = (
            (('guild_id', 'user_id'), True),
        )
