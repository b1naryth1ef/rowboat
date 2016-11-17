import psycopg2
import markovify

from holster.emitter import Priority
from disco.bot import Plugin
from disco.bot.command import CommandError
from disco.types.message import MessageTable
from disco.types.user import User
from peewee import (
    BigIntegerField, TextField, SmallIntegerField, BooleanField,
    ForeignKeyField, DateTimeField
)

from rowboat.sql import BaseModel, database


@BaseModel.register
class MessageAuthor(BaseModel):
    id = BigIntegerField(primary_key=True)
    username = TextField()
    discriminator = SmallIntegerField()
    bot = BooleanField()

    class Meta:
        indexes = (
            (('id', 'username', 'discriminator'), True),
        )

    def __str__(self):
        return u'{}#{}'.format(self.username, self.discriminator)


@BaseModel.register
class Message(BaseModel):
    id = BigIntegerField(primary_key=True)
    channel_id = BigIntegerField(index=True)
    guild_id = BigIntegerField(index=True, null=True)
    author = ForeignKeyField(MessageAuthor)
    content = TextField()
    timestamp = DateTimeField()
    edited_timestamp = DateTimeField(null=True, default=None)
    deleted = BooleanField(default=False)

    SQL = '''CREATE INDEX IF NOT EXISTS message_content_fts ON message USING gin(to_tsvector('english', content));'''


@BaseModel.register
class Reaction(BaseModel):
    message_id = BigIntegerField()
    user_id = BigIntegerField()
    emoji_id = BigIntegerField(null=True)
    emoji_name = TextField()


class MessageCachePlugin(Plugin):
    def load(self, ctx):
        self.models = ctx.get('models', {})
        super(MessageCachePlugin, self).load(ctx)

    def unload(self, ctx):
        ctx['models'] = self.models
        super(MessageCachePlugin, self).unload(ctx)

    @Plugin.listen('MessageCreate')
    def on_message_create(self, event):
        author, _ = MessageAuthor.get_or_create(
            id=event.author.id,
            defaults={
                'username': event.author.username,
                'discriminator': event.author.discriminator,
                'bot': event.author.bot,
            })

        Message.create(
            id=event.id,
            channel_id=event.channel_id,
            guild_id=(event.guild and event.guild.id),
            author=author,
            content=event.with_proper_mentions,
            timestamp=event.timestamp)

    @Plugin.listen('MessageUpdate')
    def on_message_update(self, event):
        if not event.edited_timestamp:
            return

        to_update = {
            'edited_timestamp': event.edited_timestamp
        }

        if event.content:
            to_update['content'] = event.with_proper_mentions

        Message.update(**to_update).where(Message.id == event.id).execute()

    @Plugin.listen('MessageDelete')
    def on_message_delete(self, event):
        Message.update(deleted=True).where(Message.id == event.id).execute()

    @Plugin.listen('MessageDeleteBulk')
    def on_message_delete_bulk(self, event):
        Message.update(deleted=True).where(
            Message.id << event.ids
        ).execute()

    @Plugin.listen('MessageReactionAdd', priority=Priority.BEFORE)
    def on_message_reaction_add(self, event):
        Reaction.create(
            message_id=event.message_id,
            user_id=event.user_id,
            emoji_id=event.emoji.id or None,
            emoji_name=event.emoji.name or None)

    @Plugin.listen('MessageReactionRemove', priority=Priority.BEFORE)
    def on_message_reaction_remove(self, event):
        Reaction.delete().where(
            (Reaction.message_id == event.message_id) &
            (Reaction.user_id == event.user_id) &
            (Reaction.emoji_id == (event.emoji.id or None)) &
            (Reaction.emoji_name == (event.emoji.name or None))).execute()

    @Plugin.command('sql', level=-1, global_=True)
    def command_sql(self, event):
        conn = database.obj.get_conn()

        with conn.cursor() as cur:
            try:
                cur.execute(event.codeblock.format(e=event))
            except psycopg2.Error as e:
                raise CommandError(e.pgerror)
            tbl = MessageTable()
            tbl.set_header(*[desc[0] for desc in cur.description])

            for row in cur.fetchall():
                tbl.add(*row)

            event.msg.reply(tbl.compile())

    @Plugin.command('init', '<entity:user|channel>', level=-1, group='markov', global_=True)
    def command_markov(self, event, entity):
        if isinstance(entity, User):
            q = Message.select().where(Message.author_id == entity.id).limit(500000)
        else:
            q = Message.select().where(Message.channel_id == entity.id).limit(500000)

        text = [msg.content for msg in q]
        self.models[entity.id] = markovify.NewlineText('\n'.join(text))
        event.msg.reply(u':ok_hand: created markov model for {} using {} messages'.format(entity, len(text)))

    @Plugin.command('one', '<entity:user|channel>', level=-1, group='markov', global_=True)
    def command_markov_one(self, event, entity):
        if entity.id not in self.models:
            return event.msg.reply(':warning: no model created yet for {}'.format(entity))

        sentence = self.models[entity.id].make_sentence(max_overlap_ratio=1, max_overlap_total=500)
        if not sentence:
            event.msg.reply(':warning: not enough data :(')
            return
        event.msg.reply(u'{}: {}'.format(entity, sentence))

    @Plugin.command('many', '<entity:user|channel> [count|int]', level=-1, group='markov', global_=True)
    def command_markov_many(self, event, entity, count=5):
        if entity.id not in self.models:
            return event.msg.reply(':warning: no model created yet for {}'.format(entity))

        for _ in range(int(count)):
            sentence = self.models[entity.id].make_sentence(max_overlap_total=500)
            if not sentence:
                event.msg.reply(':warning: not enough data :(')
                return
            event.msg.reply(u'{}: {}'.format(entity, sentence))

    @Plugin.command('list', level=-1, group='markov', global_=True)
    def command_markov_list(self, event):
        event.msg.reply(u'`{}`'.format(', '.join(map(str, self.models.keys()))))

    @Plugin.command('delete', '<oid:snowflake>', level=-1, group='markov', global_=True)
    def command_markov_delete(self, event, oid):
        if oid not in self.models:
            return event.msg.reply(':warning: no model with that ID')

        del self.models[oid]
        event.msg.reply(':ok_hand: deleted model')

    @Plugin.command('clear', level=-1, group='markov', global_=True)
    def command_markov_clear(self, event):
        self.models = {}
        event.msg.reply(':ok_hand: cleared models')

    @Plugin.command('backfill', '[channel:channel]', level=-1, global_=True)
    def command_backfill(self, event, channel=None):
        channel = channel or event.channel
        g = self.spawn(self.backfill_channel, channel)
        event.msg.reply(':ok_hand: started backfill on {}'.format(channel))
        event.msg.reply('{} backfill on {} completed, {} messages stored'.format(event.author.mention, channel, g.get()))

    def add_message(self, msg):
        author, _ = MessageAuthor.get_or_create(
            id=msg.author.id,
            defaults={
                'username': msg.author.username,
                'discriminator': msg.author.discriminator,
                'bot': msg.author.bot,
            })

        _, created = Message.get_or_create(
            id=msg.id,
            defaults=dict(
                channel_id=msg.channel_id,
                guild_id=(msg.guild and msg.guild.id),
                author=author,
                content=msg.with_proper_mentions,
                timestamp=msg.timestamp))

        return created

    def backfill_channel(self, channel, full=False):
        total = 0
        start = channel.last_message_id

        if not full:
            try:
                start = Message.select().where(
                    (Message.channel_id == channel.id)
                ).order_by(Message.id.asc()).limit(1).get().id
            except Message.DoesNotExist:
                pass

        for chunk in channel.messages_iter(bulk=True, before=start):
            with database.atomic():
                size = len(filter(bool, map(self.add_message, chunk)))
                total += size
                self.log.info('%s - backfilled %s messages (%s dupes)', channel, total, 100 - size)

        return total
