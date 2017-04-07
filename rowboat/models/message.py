import re
import six
import json
import uuid

from peewee import (
    BigIntegerField, ForeignKeyField, TextField, DateTimeField,
    BooleanField, UUIDField
)
from datetime import datetime, timedelta
from playhouse.postgres_ext import BinaryJSONField, ArrayField
from disco.types.base import UNSET

from rowboat.models.user import User
from rowboat.sql import BaseModel

EMOJI_RE = re.compile(r'<:.+:([0-9]+)>')


@BaseModel.register
class Message(BaseModel):
    id = BigIntegerField(primary_key=True)
    channel_id = BigIntegerField()
    guild_id = BigIntegerField(null=True)
    author = ForeignKeyField(User)
    content = TextField()
    timestamp = DateTimeField()
    edited_timestamp = DateTimeField(null=True, default=None)
    deleted = BooleanField(default=False)
    num_edits = BigIntegerField(default=0)

    mentions_new = ArrayField(BigIntegerField, default=[], null=True)
    emojis_new = ArrayField(BigIntegerField, default=[], null=True)
    attachments_new = ArrayField(TextField, default=[], null=True)

    mentions = BinaryJSONField(default=[], null=True)
    emojis = BinaryJSONField(default=[], null=True)
    attachments = BinaryJSONField(default=[], null=True)

    embeds = BinaryJSONField(default=[], null=True)

    SQL = '''
        CREATE INDEX\
                IF NOT EXISTS messages_content_fts ON messages USING gin(to_tsvector('english', content));
        CREATE INDEX\
                IF NOT EXISTS messages_content_trgm ON messages USING gin(content gin_trgm_ops);
        CREATE INDEX\
                IF NOT EXISTS messages_mentions ON messages USING gin (mentions);
        CREATE INDEX\
                IF NOT EXISTS messages_mentions ON messages USING gin (mentions);
    '''

    class Meta:
        db_table = 'messages'

        indexes = (
            (('channel_id', 'id'), True),
            (('guild_id', 'id'), True),
            (('author_id', 'id'), True),
        )

    @classmethod
    def from_disco_message_update(cls, obj):
        if not obj.edited_timestamp:
            return

        to_update = {
            'edited_timestamp': obj.edited_timestamp,
            'num_edits': cls.num_edits + 1,
            'mentions': list(obj.mentions.keys()),
            'mentions_new': list(obj.mentions.keys()),
        }

        if obj.content is not UNSET:
            to_update['content'] = obj.with_proper_mentions
            to_update['emojis_new'] = to_update['emojis'] = list(map(int, EMOJI_RE.findall(obj.content)))

        if obj.attachments is not UNSET:
            to_update['attachments_new'] = to_update['attachments'] = [i.url for i in obj.attachments.values()]

        if obj.embeds is not UNSET:
            to_update['embeds'] = [i.to_dict() for i in obj.embeds]

        cls.update(**to_update).where(cls.id == obj.id).execute()

    @classmethod
    def from_disco_message(cls, obj):
        _, created = cls.get_or_create(
            id=obj.id,
            defaults=dict(
                channel_id=obj.channel_id,
                guild_id=(obj.guild and obj.guild.id),
                author=User.from_disco_user(obj.author),
                content=obj.with_proper_mentions,
                timestamp=obj.timestamp,
                edited_timestamp=obj.edited_timestamp,
                num_edits=(0 if not obj.edited_timestamp else 1),
                mentions=list(obj.mentions.keys()),
                mentions_new=list(obj.mentions.keys()),
                emojis=list(map(int, EMOJI_RE.findall(obj.content))),
                new_emojis=list(map(int, EMOJI_RE.findall(obj.content))),
                attachments=[i.url for i in obj.attachments.values()],
                new_attachments=[i.url for i in obj.attachments.values()],
                embeds=[i.to_dict() for i in obj.embeds]))

        for user in obj.mentions.values():
            User.from_disco_user(user)

        return created

    @classmethod
    def from_disco_message_many(cls, objs):
        cls.insert_many([{
            'id': obj.id,
            'channel_id': obj.channel_id,
            'guild_id': (obj.guild and obj.guild.id),
            'author': User.from_disco_user(obj.author),
            'content': obj.with_proper_mentions,
            'timestamp': obj.timestamp,
            'edited_timestamp': obj.edited_timestamp,
            'num_edits': (0 if not obj.edited_timestamp else 1),
            'mentions': list(obj.mentions.keys()),
            'mentions_new': list(obj.mentions.keys()),
            'emojis': list(map(int, EMOJI_RE.findall(obj.content))),
            'emojis_new': list(map(int, EMOJI_RE.findall(obj.content))),
            'attachments': [i.url for i in obj.attachments.values()],
            'attachments_new': [i.url for i in obj.attachments.values()],
        } for obj in objs]).execute()

    @classmethod
    def for_channel(cls, channel):
        return cls.select().where(cls.channel_id == channel.id)


@BaseModel.register
class Reaction(BaseModel):
    message_id = BigIntegerField()
    user_id = BigIntegerField()
    emoji_id = BigIntegerField(null=True)
    emoji_name = TextField()

    class Meta:
        db_table = 'reactions'

    @classmethod
    def from_disco_reaction(cls, obj):
        return cls.create(
            message_id=obj.message_id,
            user_id=obj.user_id,
            emoji_id=obj.emoji.id or None,
            emoji_name=obj.emoji.name or None)


@BaseModel.register
class MessageArchive(BaseModel):
    FORMATS = ['txt', 'csv', 'json']

    archive_id = UUIDField(primary_key=True, default=uuid.uuid4)

    message_ids = BinaryJSONField()

    created_at = DateTimeField(default=datetime.utcnow)
    expires_at = DateTimeField(default=lambda: datetime.utcnow() + timedelta(days=7))

    class Meta:
        db_table = 'message_archives'

        indexes = (
            (('created_at', ), False),
            (('expires_at', ), False)
        )

    @classmethod
    def create_from_message_ids(cls, message_ids):
        return cls.create(message_ids=message_ids)

    @property
    def url(self):
        return 'https://rowboat.party/archive/{}.txt'.format(self.archive_id)

    def encode(self, fmt='txt'):
        from rowboat.models.user import User

        if fmt not in self.FORMATS:
            raise Exception('Invalid format {}'.format(fmt))

        q = Message.select(
            Message.id,
            Message.timestamp,
            Message.content,
            Message.deleted,
            Message.attachments,
            User
        ).join(
            User
        ).where(
            (Message.id << self.message_ids)
        )

        if fmt == 'txt':
            return u'\n'.join(map(self.encode_message_text, q))
        elif fmt == 'csv':
            return u'\n'.join(
                ['id,timestamp,author_id,author,content,deleted,attachments'] + map(self.encode_message_csv, q))
        elif fmt == 'json':
            return json.dumps({
                'messages': map(self.encode_message_json, q)
            })

    @staticmethod
    def encode_message_text(msg):
        return u'{m.timestamp} ({m.id} / {m.author.id}) {m.author}: {m.content} ({attach})'.format(
            m=msg, attach=', '.join(map(unicode, msg.attachments or [])))

    @staticmethod
    def encode_message_csv(msg):
        def wrap(i):
            return u'"{}"'.format(six.text_type(i).replace('"', '""'))

        return ','.join(map(wrap, [
            msg.id,
            msg.timestamp,
            msg.author.id,
            msg.author,
            msg.content,
            str(msg.deleted).lower(),
            ' '.join(msg.attachments or [])
        ]))

    @staticmethod
    def encode_message_json(msg):
        return dict(
            id=str(msg.id),
            timestamp=str(msg.timestamp),
            author_id=str(msg.author.id),
            username=msg.author.username,
            discriminator=msg.author.discriminator,
            content=msg.content,
            deleted=msg.deleted,
            attachments=msg.attachments)


@BaseModel.register
class StarboardEntry(BaseModel):
    message = ForeignKeyField(Message, primary_key=True)

    # Information on where this starboard message lies
    star_channel_id = BigIntegerField(null=True)
    star_message_id = BigIntegerField(null=True)

    # List of user ids who stared this message, not guarenteed to be accurate
    stars = ArrayField(BigIntegerField, default=[])

    # List of user ids who starred this message, but are blocked
    blocked_stars = ArrayField(BigIntegerField, default=[])

    blocked = BooleanField(default=False)
    dirty = BooleanField(default=False)

    SQL = '''
        CREATE INDEX\
                IF NOT EXISTS starboard_entries_stars ON starboard_entries USING gin (stars);
    '''

    class Meta:
        db_table = 'starboard_entries'

        indexes = (
            (('star_channel_id', 'star_message_id'), True),
        )

    @classmethod
    def add_star(cls, message_id, user_id):
        sql = '''
            INSERT INTO starboard_entries (message_id, stars, blocked_stars, blocked, dirty)
            VALUES (%s, ARRAY[%s], ARRAY[]::integer[], false, true)
            ON CONFLICT (message_id)
            DO UPDATE
                SET stars = array_append(starboard_entries.stars, %s), dirty = true
                WHERE NOT starboard_entries.stars @> ARRAY[%s]
            '''
        cls.raw(sql, message_id, user_id, user_id, user_id).execute()

    @classmethod
    def remove_star(cls, message_id, user_id):
        sql = '''
            UPDATE starboard_entries
                SET
                    stars = array_remove(stars, %s),
                    blocked_stars = array_remove(stars, %s),
                    dirty = true
                WHERE message_id=%s AND starboard_entries.stars @> ARRAY[%s]
        '''
        cls.raw(sql, user_id, user_id, message_id, user_id).execute()

    @classmethod
    def block_user(cls, user_id):
        sql = '''
            UPDATE starboard_entries
                SET stars = array_remove(stars, %s),
                    blocked_stars = array_append(blocked_stars, %s),
                WHERE starboard_entries.stars @> ARRAY[%s]
        '''
        cls.raw(sql, user_id, user_id, user_id)

        StarboardEntry.update(
            blocked=True,
        ).where(
            (StarboardEntry.message_id << (
                StarboardEntry.select().join(Message).where(
                    (Message.author_id == user_id)
                )
            ))
        ).execute()

    @classmethod
    def unblock_user(cls, user_id):
        sql = '''
            UPDATE starboard_entries
                SET stars = array_append(stars, %s),
                    blocked_stars = array_remove(blocked_stars, %s),
                    dirty = true
                WHERE starboard_entries.stars @> ARRAY[%s]
        '''
        cls.raw(sql, user_id, user_id, user_id)

        StarboardEntry.update(
            dirty=True,
            blocked=False,
        ).where(
            (StarboardEntry.message_id << (
                StarboardEntry.select().join(Message).where(
                    (Message.author_id == user_id)
                )
            )) & (StarboardEntry.blocked == 1)
        ).execute()
