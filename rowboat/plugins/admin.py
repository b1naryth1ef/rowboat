import six
import json
import requests

from peewee import fn
from disco.bot import CommandLevels
from disco.types.channel import Channel
from disco.types.message import MessageTable, MessageEmbed, MessageEmbedField, MessageEmbedThumbnail

from rowboat import RowboatPlugin as Plugin
from rowboat.util import C
from rowboat.util.images import get_dominant_colors_user
from rowboat.redis import rdb
from rowboat.types import Field
from rowboat.types.plugin import PluginConfig
from rowboat.models.user import User, Infraction
from rowboat.models.message import Message


EMOJI_STATS_SQL = """
WITH emojis AS (
    SELECT jsonb_array_elements_text(emojis) as id
    FROM messages WHERE guild_id={gid} AND jsonb_array_length(emojis) > 0
)
SELECT gm.emoji_id, count(*), gm.name
FROM emojis
JOIN guildemojis gm ON gm.emoji_id=emojis.id::bigint
WHERE gm.guild_id={gid}
GROUP BY gm.emoji_id
{}
LIMIT 10;
"""


class AdminConfig(PluginConfig):
    confirm_actions = Field(bool, default=True)


# TODO: unban tempbans

class AdminPlugin(Plugin):
    @Plugin.command('roles', level=CommandLevels.MOD)
    def roles(self, event):
        roles = []
        for role in event.guild.roles.values():
            roles.append('{} - {}'.format(role.id, role.name))
        return event.msg.reply('```{}```'.format('\n'.join(roles)))

    @Plugin.command('kick', '<user:user> [reason:str...]', level=CommandLevels.MOD)
    def kick(self, event, user, reason=None):
        """
        Kick a user from the server (with an optional reason for the modlog).
        """
        member = event.guild.get_member(user)
        if member:
            Infraction.kick(self, event, member, reason)
            if event.config.confirm_actions:
                event.msg.reply(u':ok_hand: kicked {} for `{}`'.format(user, reason or 'no reason given'))
        else:
            event.msg.reply(':warning: Invalid user!')

    @Plugin.command('ban', '<user:user> [reason:str...]', level=CommandLevels.MOD)
    @Plugin.command('forceban', '<user:snowflake> [reason:str...]', level=CommandLevels.MOD)
    def ban(self, event, user, reason=None):
        """
        Ban a user from the server (with an optional reason for the modlog).
        """

        if isinstance(user, (int, long)):
            Infraction.ban(self, event, user, reason, guild=event.guild)
        else:
            member = event.guild.get_member(user)
            if member:
                Infraction.ban(self, event, member, reason, guild=event.guild)
            else:
                event.msg.reply(':warning: Invalid user!')
                return

        event.msg.reply(u':ok_hand: banned {} for `{}`'.format(user, reason or 'no reason given'))

    @Plugin.command('softban', '<user:user> [reason:str...]', level=CommandLevels.MOD)
    def softban(self, event, user, reason=None):
        """
        Ban then unban a user from the server (with an optional reason for the modlog).
        """

        member = event.guild.get_member(user)
        if member:
            Infraction.softban(self, event, member, reason)
            if event.config.confirm_actions:
                event.msg.reply(u':ok_hand: soft-banned {} for `{}`'.format(user, reason or 'no reason given'))
        else:
            event.msg.reply(':warning: Invalid user!')

    @Plugin.command('tempban', '<user:user> <duration:duration> [reason:str...]', level=CommandLevels.MOD)
    def tempban(self, event, duration, user, reason=None):
        """
        Ban a user from the server for a given duration (with an optional reason for the modlog).
        """

        member = event.guild.get_member(user)
        if member:
            Infraction.tempban(self, event, member, reason, duration)
            if event.config.confirm_actions:
                event.msg.reply(u':ok_hand: temp-banned {} for `{}`'.format(user, reason or 'no reason given'))
        else:
            event.msg.reply(':warning: Invalid user!')

    def archive_messages(self, message_q, fmt, expiry=365):
        def encode_txt(msg):
            return u'{m.timestamp} {m.author}: {m.content}'.format(m=msg)

        def encode_csv(msg):
            def wrap(i):
                return u'"{}"'.format(six.text_type(i).replace('"', '""'))

            return ','.join(map(wrap, [
                msg.id,
                msg.timestamp,
                msg.author.id,
                msg.author,
                msg.content,
                str(msg.deleted).lower(),
            ]))

        def encode_json(msg):
            return {
                'id': str(msg.id),
                'timestamp': str(msg.timestamp),
                'user_id': str(msg.author.id),
                'username': msg.author.username,
                'discriminator': msg.author.discriminator,
                'content': msg.content,
                'deleted': msg.deleted,
            }

        msgs = list(reversed(message_q))

        if fmt == 'txt':
            data = map(encode_txt, msgs)
            result = u'\n'.join(data)
        elif fmt == 'csv':
            data = map(encode_csv, msgs)
            data = ['id,timestamp,author_id,author,content,deleted'] + data
            result = u'\n'.join(data)
        elif fmt == 'json':
            data = list(map(encode_json, msgs))
            result = json.dumps({
                'count': len(data),
                'messages': data,
            })

        r = requests.post('http://dpaste.com/api/v2/', data={
            'content': result,
            'expiry_days': expiry,
            'poster': 'Rowboat',
        })
        r.raise_for_status()
        return r.content.strip() + '.txt'

    @Plugin.command('archive here', '[size:int] [fmt:str]', level=CommandLevels.MOD, context={'mode': 'all'})
    @Plugin.command('archive user', '<user:user|snowflake> [size:int] [fmt:str]', level=CommandLevels.MOD, context={'mode': 'user'})
    @Plugin.command('archive channel', '<channel:channel|snowflake> [size:int] [fmt:str]', level=CommandLevels.MOD, context={'mode': 'channel'})
    def archive(self, event, size=50, fmt='txt', mode=None, user=None, channel=None):
        """
        Archives messages to a given format (txt, csv, json).
        """

        if fmt not in ('txt', 'csv', 'json'):
            return event.msg.reply(':warning: Invalid message format, needs to be one of txt, csv, json')

        if 0 > size >= 15000:
            return event.msg.reply(':warning: Too many messages, must be between 1-15000')

        q = Message.select().join(User).order_by(Message.id.desc()).limit(size)

        if mode in ('all', 'channel'):
            q = q.where((Message.channel_id == (channel or event.channel).id))
        else:
            q = q.where(
                (Message.author_id == (user if isinstance(user, (int, long)) else user.id)) &
                (Message.guild_id == event.guild.id)
            )

        url = self.archive_messages(q, fmt, expiry=90)
        event.msg.reply('OK, archived {} messages at {}'.format(size, url))

    @Plugin.command('clean all', '[size:int]', level=CommandLevels.MOD, context={'mode': 'all'})
    @Plugin.command('clean bots', '[size:int]', level=CommandLevels.MOD, context={'mode': 'bots'})
    @Plugin.command('clean user', '<user:user> [size:int]', level=CommandLevels.MOD, context={'mode': 'user'})
    def clean(self, event, user=None, size=25, typ=None, mode='all'):
        if 0 > size >= 10000:
            return event.msg.reply(':warning: Too many messages, must be between 1-10000')

        lock = rdb.lock('clean-{}'.format(event.channel.id))
        if not lock.acquire(blocking=False):
            return event.msg.reply(':warning: already running a clean on this channel')

        try:
            query = Message.select().where(
                (Message.deleted >> False) &
                (Message.channel_id == event.channel.id)
            ).join(User).order_by(Message.timestamp.desc()).limit(size)

            if mode == 'bots':
                query = query.where((User.bot >> True))
            elif mode == 'user':
                query = query.where((User.user_id == user.id))

            msgs = list(reversed(query))
            event.channel.delete_messages(msgs)
            event.msg.reply(':wastebasket: Ok, deleted {} messages'.format(len(msgs))).after(5).delete()
        finally:
            lock.release()

    @Plugin.command('msgstats', '<user:user> [ctx:channel|snowflake|str]', level=CommandLevels.MOD)
    def msgstats(self, event, user, ctx=None):
        base_query = Message.select().where(
            (Message.author_id == user.id)
        )

        if ctx:
            if isinstance(ctx, Channel):
                base_query = base_query.where((Message.channel_id == ctx.id))
            elif isinstance(ctx, int):
                if ctx not in self.state.guilds:
                    return event.msg.reply(u':warning: unknown guild {}'.format(C(ctx)))
                base_query = base_query.where((Message.guild_id == ctx))
            elif ctx == 'channel':
                base_query = base_query.where((Message.channel_id == event.channel.id))
            elif ctx == 'guild':
                base_query = base_query.where((Message.guild_id == event.guild.id))
            else:
                return event.msg.reply(u':warning: invalid context {}'.format(C(ctx)))

        # Grab total messages/characters
        q = base_query.select(
            fn.Count('*'),
            fn.Sum(fn.char_length(Message.content)),
            fn.Sum(fn.jsonb_array_length(Message.emojis)),
        ).tuples()[0]

        emojis = list(Message.raw("""
            SELECT count(i)
            FROM (
                SELECT jsonb_array_elements(emojis)
                FROM messages WHERE author_id=%s
            ) i
            GROUP BY i
        """, (user.id, )).tuples())

        deleted = base_query.where((Message.deleted >> True)).count()

        embed = MessageEmbed()
        embed.fields.append(
            MessageEmbedField(name='Total Messages', value=q[0], inline=True))
        embed.fields.append(
            MessageEmbedField(name='Total Characters', value=q[1], inline=True))
        embed.fields.append(
            MessageEmbedField(name='Deleted Messages', value=deleted, inline=True))
        embed.fields.append(
            MessageEmbedField(name='Total Custom Emoji', value=sum(i[0] for i in emojis), inline=True))
        embed.fields.append(
            MessageEmbedField(name='Unique Emojis Used', value=len(emojis), inline=True))

        embed.thumbnail = MessageEmbedThumbnail(url=user.avatar_url)
        embed.color = get_dominant_colors_user(user)
        event.msg.reply('', embed=embed)

    @Plugin.command('emojistats most', level=CommandLevels.MOD, context={'mode': 'most'})
    @Plugin.command('emojistats least', level=CommandLevels.MOD, context={'mode': 'least'})
    def emojistats(self, event, mode='default'):
        if mode == 'most':
            sql = EMOJI_STATS_SQL.format('ORDER BY 2 DESC', gid=event.guild.id)
        else:
            sql = EMOJI_STATS_SQL.format('ORDER BY 2 ASC', gid=event.guild.id)

        q = list(Message.raw(sql).tuples())

        tbl = MessageTable()
        tbl.set_header('Count', 'Name', 'ID')

        for emoji_id, count, name in q:
            tbl.add(count, name, emoji_id)

        event.msg.reply(tbl.compile())
