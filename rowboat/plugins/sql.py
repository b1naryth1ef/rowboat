import time
import psycopg2
import markovify

from holster.emitter import Priority
from disco.api.http import APIException
from disco.types.message import MessageTable
from disco.types.user import User as DiscoUser

from rowboat.plugins import BasePlugin as Plugin
from rowboat.sql import database
from rowboat.models.guild import GuildEmoji
from rowboat.models.channel import Channel
from rowboat.models.message import Message, Reaction


# TODO: rename this lol
class SQLPlugin(Plugin):
    def load(self, ctx):
        self.models = ctx.get('models', {})
        self.backfill_status = None
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
        for channel in event.channels.values():
            Channel.from_disco_channel(channel)

        for emoji in event.emojis.values():
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

                event.msg.reply('```' + tbl.compile() + '```\n_took {}ms_\n'.format(int(dur * 1000)))
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

    @Plugin.command('backfill global', level=-1, global_=True)
    def command_backfill_global(self, event):
        if self.backfill_status:
            return event.msg.reply(':warning: already backfilling')

        event.msg.reply(':ok_hand: starting backfill on {} channels'.format(len(self.state.channels)))

        self.backfill_status = [None, len(self.state.channels), 0, 0]
        for channel in list(self.state.channels.values()):
            self.backfill_status[0] = channel
            self.backfill_status[2] += 1
            try:
                self.backfill_channel(channel)
            except APIException:
                continue

        self.backfill_status = None

    @Plugin.command('backfill status', level=-1, global_=True)
    def command_backfill_status(self, event):
        if not self.backfill_status:
            return event.msg.reply(':warning: no backfill')

        channel, chan_count, chan_current, messages = self.backfill_status
        if chan_count == 1:
            event.msg.reply('Backfilling {}, loaded {} messages so far'.format(channel, messages))
        else:
            event.msg.reply('[{}/{}] current channel {} ({}), {} messages so far'.format(
                chan_current,
                chan_count,
                channel,
                channel.guild.name if channel.guild_id else '',
                messages
            ))

    @Plugin.command('backfill one', '[channel:channel]', level=-1, global_=True)
    def command_backfill(self, event, channel=None):
        if self.backfill_status:
            return event.msg.reply(':warning: already backfilling')

        channel = channel or event.channel
        self.backfill_status = [channel, 1, 1, 0]
        g = self.spawn(self.backfill_channel, channel)
        event.msg.reply(':ok_hand: started backfill on {}'.format(channel))
        count = g.get()
        self.backfill_status = None
        event.msg.reply('{} backfill on {} completed, {} messages stored'.format(event.author.mention, channel, count))

    def backfill_channel(self, channel, full=False):
        self.backfill_status[3] = 0
        total = 0
        start = channel.last_message_id

        if not full:
            try:
                start = Message.select().where(
                    (Message.channel_id == channel.id)
                ).order_by(Message.id.asc()).limit(1).get().id
            except Message.DoesNotExist:
                pass

        if not start:
            return

        for chunk in channel.messages_iter(bulk=True, before=start):
            existing = [i.id for i in Message.select(Message.id).where((Message.id << [i.id for i in chunk]))]
            Message.from_disco_message_many([i for i in chunk if i.id not in existing])
            total += len(chunk)
            self.backfill_status[3] = total
            self.log.info('%s - backfilled %s messages', channel, total)

        Channel.update(first_message_id=Channel.generate_first_message_id(channel.id)).where(
            Channel.channel_id == channel.id
        ).execute()

        return total
