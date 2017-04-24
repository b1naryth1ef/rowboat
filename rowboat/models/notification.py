import json
import arrow

from datetime import datetime
from holster.enum import Enum
from peewee import IntegerField, DateTimeField
from playhouse.postgres_ext import BinaryJSONField, BooleanField

from rowboat.sql import BaseModel
from rowboat.redis import rdb

NotificationTypes = Enum(
    GENERIC=1,
    CONNECT=2,
    RESUME=3,
    GUILD_JOIN=4,
    GUILD_LEAVE=5,
)


@BaseModel.register
class Notification(BaseModel):
    Types = NotificationTypes

    type_ = IntegerField(db_column='type')
    metadata = BinaryJSONField(default={})
    read = BooleanField(default=False)
    created_at = DateTimeField(default=datetime.utcnow)

    class Meta:
        db_table = 'notifications'

        indexes = (
            (('created_at', 'read'), False),
        )

    @classmethod
    def get_unreads(cls, limit=25):
        return cls.select().where(
            cls.read == 0,
        ).order_by(
            cls.created_at.asc()
        ).limit(limit)

    @classmethod
    def dispatch(cls, typ, **kwargs):
        obj = cls.create(
            type_=typ,
            metadata=kwargs
        )

        rdb.publish('notifications', json.dumps(obj.to_user()))
        return obj

    def to_user(self):
        data = {}

        data['id'] = self.id
        data['date'] = arrow.get(self.created_at).humanize()

        if self.type_ == self.Types.GENERIC:
            data['title'] = self.metadata.get('title', 'Generic Notification')
            data['content'] = self.metadata.get('content', '').format(m=self.metadata)
        elif self.type_ == self.Types.CONNECT:
            data['title'] = u'{} connected'.format(
                'Production' if self.metadata['env'] == 'prod' else 'Testing')
            data['content'] = ', '.join(self.metadata['trace'])
        elif self.type_ == self.Types.RESUME:
            data['title'] = u'{} resumed'.format(
                'Production' if self.metadata['env'] == 'prod' else 'Testing')
            data['content'] = ', '.join(self.metadata['trace'])

        return data
