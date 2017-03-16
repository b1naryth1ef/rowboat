import json
import yaml

from peewee import (
    BigIntegerField, CharField, TextField, BooleanField, DateTimeField, CompositeKey, BlobField
)
from datetime import datetime
from playhouse.postgres_ext import BinaryJSONField

from rowboat.sql import BaseModel
from rowboat.redis import rdb
from rowboat.models.user import User


@BaseModel.register
class Guild(BaseModel):
    guild_id = BigIntegerField(primary_key=True)
    owner_id = BigIntegerField(null=True)
    name = TextField(null=True)
    icon = TextField(null=True)
    splash = TextField(null=True)
    region = TextField(null=True)

    last_ban_sync = DateTimeField(null=True)

    # Rowboat specific data
    config = BinaryJSONField(null=True)
    config_raw = BlobField(null=True)

    enabled = BooleanField(default=True)
    whitelist = BinaryJSONField(default=[])

    added_at = DateTimeField(default=datetime.utcnow)

    class Meta:
        db_table = 'guilds'

    @classmethod
    def with_id(cls, guild_id):
        return cls.get(guild_id=guild_id)

    @classmethod
    def setup(cls, guild):
        return cls.create(
            guild_id=guild.id,
            owner_id=guild.owner_id,
            name=guild.name,
            icon=guild.icon,
            splash=guild.splash,
            region=guild.region,
            config={},
            config_raw='')

    def update_config(self, actor_id, raw):
        from rowboat.types.guild import GuildConfig

        parsed = yaml.load(raw)
        GuildConfig(parsed).validate()

        GuildConfigChange.create(
            user_id=actor_id,
            guild_id=self.guild_id,
            before_raw=self.config_raw,
            after_raw=raw)

        self.update(config=parsed, config_raw=raw).where(Guild.guild_id == self.guild_id).execute()
        self.emit_update()

    def emit_update(self):
        rdb.publish('guild-updates', json.dumps({
            'type': 'UPDATE',
            'id': self.guild_id,
        }))

    def sync(self, guild):
        updates = {}

        for key in ['owner_id', 'name', 'icon', 'splash', 'region']:
            if getattr(guild, key) != getattr(self, key):
                updates[key] = getattr(guild, key)

        if updates:
            Guild.update(**updates).where(Guild.guild_id == self.guild_id).execute()

    def get_config(self, refresh=False):
        from rowboat.types.guild import GuildConfig

        if refresh:
            self.config = Guild.select(Guild.config).where(Guild.guild_id == self.guild_id).get().config

        if refresh or not hasattr(self, '_cached_config'):
            self._cached_config = GuildConfig(self.config)
        return self._cached_config

    def sync_bans(self, guild):
        try:
            bans = guild.get_bans()
        except:
            return

        for ban in bans.values():
            GuildBan.ensure(guild, ban)

        # Update last synced time
        Guild.update(
            last_ban_sync=datetime.utcnow()).where(Guild.guild_id == self.guild_id).execute()


@BaseModel.register
class GuildEmoji(BaseModel):
    emoji_id = BigIntegerField(primary_key=True)
    guild_id = BigIntegerField()
    name = CharField(index=True)

    require_colons = BooleanField()
    managed = BooleanField()
    roles = BinaryJSONField()

    deleted = BooleanField(default=False)

    class Meta:
        db_table = 'guildemojis'

    @classmethod
    def from_disco_guild_emoji(cls, emoji, guild_id=None):
        try:
            ge = cls.get(emoji_id=emoji.id)
            new = False
        except cls.DoesNotExist:
            ge = cls(emoji_id=emoji.id)
            new = True

        ge.guild_id = guild_id or emoji.guild_id
        ge.name = emoji.name
        ge.require_colons = emoji.require_colons
        ge.managed = emoji.managed
        ge.roles = emoji.roles
        ge.save(force_insert=new)
        return ge


@BaseModel.register
class GuildBan(BaseModel):
    user_id = BigIntegerField()
    guild_id = BigIntegerField()
    reason = TextField(null=True)

    class Meta:
        db_table = 'guildbans'
        primary_key = CompositeKey('user_id', 'guild_id')

    @classmethod
    def ensure(cls, guild, ban):
        User.ensure(ban.user)
        obj, _ = cls.get_or_create(guild_id=guild.id, user_id=ban.user.id, defaults=dict(reason=ban.reason))
        return obj


@BaseModel.register
class GuildConfigChange(BaseModel):
    user_id = BigIntegerField(null=True)
    guild_id = BigIntegerField()

    before_raw = BlobField(null=True)
    after_raw = BlobField()

    created_at = DateTimeField(default=datetime.utcnow)

    class Meta:
        db_table = 'guild_config_changes'

        indexes = (
            (('user_id', 'guild_id'), False),
        )

    def rollback_to(self):
        Guild.update(
            config_raw=self.after_raw,
            config=yaml.load(self.after_raw)
        ).where(Guild.guild_id == self.guild_id).execute()

    def revert(self):
        Guild.update(
            config_raw=self.before_raw,
            config=yaml.load(self.before_raw)
        ).where(Guild.guild_id == self.guild_id).execute()


@BaseModel.register
class GuildMemberBackup(BaseModel):
    user_id = BigIntegerField()
    guild_id = BigIntegerField()

    nick = CharField(null=True)
    roles = BinaryJSONField(default=[])

    mute = BooleanField(null=True)
    deaf = BooleanField(null=True)

    class Meta:
        db_table = 'guild_member_backups'
        primary_key = CompositeKey('user_id', 'guild_id')

    @classmethod
    def create_from_member(cls, member):
        cls.delete().where(
            (cls.user_id == member.user.id) &
            (cls.guild_id == member.guild_id)
        ).execute()

        return cls.create(
            user_id=member.user.id,
            guild_id=member.guild_id,
            nick=member.nick,
            roles=member.roles,
            mute=member.mute,
            deaf=member.deaf,
        )
