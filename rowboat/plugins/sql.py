import time
import gevent
import psycopg2
import markovify

from gevent.pool import Pool
from holster.enum import Enum
from holster.emitter import Priority
from datetime import datetime

from disco.types.message import MessageTable
from disco.types.user import User as DiscoUser
from disco.types.guild import Guild as DiscoGuild
from disco.types.channel import Channel as DiscoChannel, MessageIterator
from disco.util.snowflake import to_datetime, from_datetime

from rowboat.plugins import BasePlugin as Plugin
from rowboat.sql import database
from rowboat.models.guild import GuildEmoji
from rowboat.models.channel import Channel
from rowboat.models.message import Message, Reaction
from rowboat.util.input import parse_duration


class SQLPlugin(Plugin):
    def load(self, ctx):
        self.models = ctx.get('models', {})
        self.backfills = {}
        super(SQLPlugin, self).load(ctx)

    def unload(self, ctx):
        ctx['models'] = self.models
        super(SQLPlugin, self).unload(ctx)

    @Plugin.listen('MessageCreate')
    def on_message_create(self, event):
        Message.from_disco_message(event.message)

    @Plugin.listen('MessageUpdate')
    def on_message_update(self, event):
        Message.from_disco_message_update(event.message)

    @Plugin.listen('MessageDelete')
    def on_message_delete(self, event):
        Message.update(deleted=True).where(Message.id == event.id).execute()

    @Plugin.listen('MessageDeleteBulk')
    def on_message_delete_bulk(self, event):
        Message.update(deleted=True).where((Message.id << event.ids)).execute()

    @Plugin.listen('MessageReactionAdd', priority=Priority.BEFORE)
    def on_message_reaction_add(self, event):
        Reaction.from_disco_reaction(event)

    @Plugin.listen('MessageReactionRemove', priority=Priority.BEFORE)
    def on_message_reaction_remove(self, event):
        Reaction.delete().where(
            (Reaction.message_id == event.message_id) &
            (Reaction.user_id == event.user_id) &
            (Reaction.emoji_id == (event.emoji.id or None)) &
            (Reaction.emoji_name == (event.emoji.name or None))).execute()

    @Plugin.listen('MessageReactionRemoveAll')
    def on_message_reaction_remove_all(self, event):
        Reaction.delete().where((Reaction.message_id == event.message_id)).execute()

    @Plugin.listen('GuildEmojisUpdate', priority=Priority.BEFORE)
    def on_guild_emojis_update(self, event):
        ids = []

        for emoji in event.emojis:
            GuildEmoji.from_disco_guild_emoji(emoji, event.guild_id)
            ids.append(emoji.id)

        GuildEmoji.update(deleted=True).where(
            (GuildEmoji.guild_id == event.guild_id) &
            (~(GuildEmoji.emoji_id << ids))
        ).execute()

    @Plugin.listen('GuildCreate')
    def on_guild_create(self, event):
        for channel in list(event.channels.values()):
            Channel.from_disco_channel(channel)

        for emoji in list(event.emojis.values()):
            GuildEmoji.from_disco_guild_emoji(emoji, guild_id=event.guild.id)

    @Plugin.listen('GuildDelete')
    def on_guild_delete(self, event):
        if event.deleted:
            Channel.update(deleted=True).where(
                Channel.guild_id == event.id
            ).execute()

    @Plugin.listen('ChannelCreate')
    def on_channel_create(self, event):
        Channel.from_disco_channel(event.channel)

    @Plugin.listen('ChannelUpdate')
    def on_channel_update(self, event):
        Channel.from_disco_channel(event.channel)

    @Plugin.listen('ChannelDelete')
    def on_channel_delete(self, event):
        Channel.update(deleted=True).where(Channel.channel_id == event.channel.id).execute()

    @Plugin.command('sql', level=-1, global_=True)
    def command_sql(self, event):
        conn = database.obj.get_conn()

        try:
            tbl = MessageTable(codeblock=False)

            with conn.cursor() as cur:
                start = time.time()
                cur.execute(event.codeblock.format(e=event))
                dur = time.time() - start
                tbl.set_header(*[desc[0] for desc in cur.description])

                for row in cur.fetchall():
                    tbl.add(*row)

                result = tbl.compile()
                if len(result) > 1900:
                    return event.msg.reply(
                        '_took {}ms_'.format(int(dur * 1000)),
                        attachment=('result.txt', result))

                event.msg.reply('```' + result + '```\n_took {}ms_\n'.format(int(dur * 1000)))
        except psycopg2.Error as e:
            event.msg.reply('```{}```'.format(e.pgerror))

    @Plugin.command('init', '<entity:user|channel>', level=-1, group='markov', global_=True)
    def command_markov(self, event, entity):
        if isinstance(entity, DiscoUser):
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

    @Plugin.command('global', '<duration:str> [pool:int]', level=-1, global_=True, context={'mode': 'global'}, group='recover')
    @Plugin.command('here', '<duration:str> [pool:int]', level=-1, global_=True, context={'mode': 'here'}, group='recover')
    def command_recover(self, event, duration, pool=4, mode=None):
        if mode == 'global':
            channels = list(self.state.channels.values())
        else:
            channels = list(event.guild.channels.values())

        start_at = parse_duration(duration, negative=True)

        pool = Pool(pool)

        total = len(channels)
        count = 0
        msg = event.msg.reply('Recovery Status: 0/{}'.format(total))

        def updater():
            last = count

            while True:
                if last != count:
                    last = count
                    msg.edit('Recovery Status: {}/{}'.format(count, total))
                gevent.sleep(5)

        u = self.spawn(updater)

        try:
            for channel in channels:
                pool.wait_available()
                r = Recovery(self.log, channel, start_at)
                pool.spawn(r.run)
                count += 1
        finally:
            u.kill()

        msg.edit('RECOVERY COMPLETED')

    @Plugin.command('backfill channel', '[channel:channel] [mode:str] [direction:str]', level=-1, global_=True)
    def command_backfill_channel(self, event, channel=None, mode=None, direction=None):
        channel = channel or event.channel
        mode = Backfill.Mode.get(mode) if mode else Backfill.Mode.SPARSE
        direction = Backfill.Direction.get(direction) if direction else Backfill.Direction.UP

        if not mode:
            return event.msg.reply(u':warning: unknown mode')

        if not direction:
            return event.msg.reply(u':warning: unknown direction')

        if channel.id in self.backfills:
            event.msg.reply(':warning: a backfill is already running for that channel')
            return

        self.backfills[channel.id] = Backfill(self.log, channel, mode)
        self.spawn(self.backfills[channel.id].start).get()
        event.msg.reply('Completed backfill: {} scanned / {} inserted'.format(
            self.backfills[channel.id].scanned,
            self.backfills[channel.id].inserted,
        ))
        del self.backfills[channel.id]

    @Plugin.command('backfill guild', '[guild:guild] [mode:str] [direction:str]', level=-1, global_=True)
    def command_backfill_guild(self, event, guild=None, mode=None, direction=None):
        guild = guild or event.guild
        mode = Backfill.Mode.get(mode) if mode else Backfill.Mode.SPARSE
        direction = Backfill.Direction.get(direction) if direction else Backfill.Direction.UP

        if not mode:
            return event.msg.reply(u':warning: unknown mode')

        if not direction:
            return event.msg.reply(u':warning: unknown direction')

        p = gevent.pool.Pool(4)

        for channel in guild.channels.values():
            if channel.id in self.backfills:
                continue

            if channel.is_voice:
                continue

            def backfill_one(c):
                self.backfills[c.id] = Backfill(self.log, c, mode)
                self.spawn(self.backfills[c.id].start).get()
                event.msg.reply(u'Completed backfill on {}: {} scanned / {} inserted'.format(
                    c,
                    self.backfills[c.id].scanned,
                    self.backfills[c.id].inserted,
                ))
                del self.backfills[c.id]

            p.add(self.spawn(backfill_one, channel))

        p.join()
        event.msg.reply(u'Completed backfill on {}'.format(guild.name))

    @Plugin.command('words', '<target:user|channel|guild>', level=-1)
    def words(self, event, target):
        if isinstance(target, DiscoUser):
            q = 'author_id'
        elif isinstance(target, DiscoChannel):
            q = 'channel_id'
        elif isinstance(target, DiscoGuild):
            q = 'guild_id'
        else:
            raise Exception("You should not be here")

        sql = """
            SELECT word, count(*)
            FROM (
                SELECT regexp_split_to_table(content, '\s') as word
                FROM messages
                WHERE {}=%s
                LIMIT 3000000
            ) t
            GROUP BY word
            ORDER BY 2 DESC
            LIMIT 30
        """.format(q)

        t = MessageTable()
        t.set_header('Word', 'Count')

        for word, count in Message.raw(sql, (target.id, )).tuples():
            if '```' in word:
                continue
            t.add(word, count)

        event.msg.reply(t.compile())


class Recovery(object):
    def __init__(self, log, channel, start_dt, end_dt=None):
        self.log = log
        self.channel = channel
        self.start_dt = start_dt
        self.end_dt = end_dt or datetime.utcnow()

    def run(self):
        self.log.info('Starting recovery on channel %s (%s -> %s)', self.channel.id, self.start_dt, self.end_dt)

        msgs = self.channel.messages_iter(
            bulk=True,
            direction=MessageIterator.Direction.DOWN,
            after=str(from_datetime(self.start_dt))
        )

        for chunk in msgs:
            print Message.from_disco_message_many(chunk, safe=True)

            if to_datetime(chunk[-1].id) > self.end_dt:
                break


class Backfill(object):
    Mode = Enum('FULL', 'SPARSE', 'BACKFILL')
    Direction = Enum('UP', 'DOWN')

    def __init__(self, log, channel, mode, direction=Direction.UP):
        self.log = log
        self.channel = channel
        self.mode = mode
        self.direction = direction

        self.scanned = 0
        self.inserted = 0

    def start(self):
        # First, generate a starting point
        self.log.info('Starting %s backfill on %s going %s', self.mode, self.channel, self.direction)

        start = None

        if self.mode in (Backfill.Mode.FULL, Backfill.Mode.SPARSE):
            # If we are going newest - oldest
            if self.direction is Backfill.Direction.UP:
                start = self.channel.last_message_id
                if not start:
                    self.log.warning('Invalid last_message_id for {}'.format(self.channel))
                    return
            else:
                start = 0
        elif self.mode is Backfill.Mode.BACKFILL:
            q = Message.for_channel(self.channel)
            if self.direction is Backfill.Direction.UP:
                q = q.order_by(Message.id.asc()).limit(1).get().id
            else:
                q = q.order_by(Message.id.desc()).limit(1).get().id

        if self.direction is Backfill.Direction.UP:
            msgs = self.channel.messages_iter(bulk=True, before=start)
        else:
            msgs = self.channel.messages_iter(bulk=True, after=start)

        for chunk in msgs:
            self.scanned += len(chunk)
            existing = {i.id for i in Message.select(Message.id).where((Message.id << [i.id for i in chunk]))}

            if len(existing) < len(chunk):
                Message.from_disco_message_many([i for i in chunk if i.id not in existing])
                self.inserted += len(chunk) - len(existing)

            if len(existing) and self.mode is Backfill.Mode.BACKFILL:
                self.log.info('Found %s existing messages, breaking', len(existing))
                break

            if len(existing) == len(chunk) and self.mode is Backfill.Mode.Sparse:
                self.log.info('Found %s existing messages, breaking', len(existing))
                break
