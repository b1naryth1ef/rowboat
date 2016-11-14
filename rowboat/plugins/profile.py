from peewee import (
    BigIntegerField, CharField, TextField, DateTimeField, CompositeKey
)

from holster.enum import Enum
from rowboat.sql import BaseModel
from playhouse.postgres_ext import BinaryJSONField
from datetime import datetime

from rowboat import RowboatPlugin as Plugin
from rowboat.util import C


Platforms = Enum(
    'steam',
    'battlenet',
)


@BaseModel.register
class Profile(BaseModel):
    user_id = BigIntegerField()
    platform = CharField()
    username = TextField()
    metadata = BinaryJSONField()

    created_at = DateTimeField(default=datetime.utcnow)

    class Meta:
        primary_key = CompositeKey('user_id', 'platform')

        indexes = (
            (('username', ), False),
        )

    @staticmethod
    def validate_username(platform, username):
        if platform is Platforms.BATTLENET:
            if not username.count('#') == 1:
                return False

            username, tag = username.split('#')
            if not len(tag) >= 4 and tag.isdigit():
                return False

            return True


class ProfilePlugin(Plugin):
    @Plugin.command('add', '<platform:str> <username:str>', group='profile', global_=True)
    def profile_add(self, event, platform, username):
        platform = Platforms.get(platform)
        if not platform:
            return event.msg.reply(':warning: unknown platform: `{}`'.format(C(platform)))

        try:
            Profile.select().where(
                (Profile.user_id == event.author.id) &
                (Profile.platform == platform.value)
            ).get()
            return event.msg.reply(':warning: you already have a linked profile for {}'.format(platform))
        except Profile.DoesNotExist:
            pass

        if not Profile.validate_username(platform, username):
            return event.msg.reply(':warning: invalid username provided')

        Profile.create(
            user_id=event.author.id,
            platform=platform.value,
            username=username,
            metadata={},
        )

        event.msg.reply(':ok_hand: Ok, your {} profile is now linked!'.format(platform))

    @Plugin.command('rmv', '<platform:str>', group='profile', global_=True, aliases=['remove'])
    def profile_rmv(self, event, platform):
        platform = Platforms.get(platform)
        if not platform:
            return event.msg.reply(':warning: unknown platform: `{}`'.format(C(platform)))

        try:
            p = Profile.select().where(
                (Profile.user_id == event.author.id) &
                (Profile.platform == platform.value)
            ).get()
            p.delete_instance()
            return event.msg.reply(':ok_hand: removed your linked {} profile'.format(platform))
        except Profile.DoesNotExist:
            return event.msg.reply(":warning: you don't have a linked {} profile".format(platform))

    @Plugin.command('list', group='profile', global_=True)
    def profile_info(self, event, platform=None):
        data = []
        for profile in Profile.select().where(Profile.user_id == event.author.id):
            platform = Platforms.get(profile.platform)
            data.append('{} - {}'.format(platform.name.title(), profile.username))

        event.msg.reply('\n'.join(data))
