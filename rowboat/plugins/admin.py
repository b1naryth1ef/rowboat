import six
import json
import requests

from peewee import fn
from disco.bot import CommandLevels
from disco.types.channel import Channel
from disco.types.message import MessageTable, MessageEmbed, MessageEmbedField, MessageEmbedAuthor

from rowboat import RowboatPlugin as Plugin
from rowboat.util import C
from rowboat.redis import rdb
# from rowboat.sql import pg_regex_i
from rowboat.types.plugin import PluginConfig
from rowboat.plugins.messages import Message, User


class AdminConfig(PluginConfig):
    pass


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

        u = event.guild.get_member(user)
        if u:
            self.bot.plugins.get('ModLogPlugin').create_debounce(event, user, 'kick',
                actor=str(event.author),
                reason=reason or 'no reason')
            u.kick()
        else:
            event.msg.reply(':warning: Invalid user!')

    @Plugin.command('ban', '<user:user> [reason:str...]', level=CommandLevels.MOD)
    def ban(self, event, user, reason=None):
        """
        Ban a user from the server (with an optional reason for the modlog).
        """

        u = event.guild.get_member(user)
        if u:
            self.bot.plugins.get('ModLogPlugin').create_debounce(event, user, 'ban_reason',
                actor=str(event.author),
                reason=reason or 'no reason')
            u.ban()
        else:
            event.msg.reply(':warning: Invalid user!')

    @Plugin.command('archive', '[size:int] [fmt:str]', level=CommandLevels.MOD)
    def archive(self, event, size=50, fmt='txt'):
        """
        Archives messages to a given format (txt, csv, json).
        """

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
            ]))

        def encode_json(msg):
            return {
                'id': str(msg.id),
                'timestamp': str(msg.timestamp),
                'user_id': str(msg.author.id),
                'username': msg.author.username,
                'discriminator': msg.author.discriminator,
                'content': msg.content
            }

        if fmt not in ('txt', 'csv', 'json'):
            return event.msg.reply(':warning: Invalid message format, needs to be one of txt, csv, json')

        if 0 > size >= 5000:
            return event.msg.reply(':warning: Too many messages, must be between 1-5000')

        msgs = list(reversed(Message.select().where(
            (Message.deleted >> False) &
            (Message.channel_id == event.channel.id)
        ).join(User).order_by(Message.timestamp.desc()).limit(size)))

        if fmt == 'txt':
            data = map(encode_txt, msgs)
            result = u'\n'.join(data)
        elif fmt == 'csv':
            data = map(encode_csv, msgs)
            data = ['id,timestamp,author_id,author,content'] + data
            result = u'\n'.join(data)
        elif fmt == 'json':
            data = list(map(encode_json, msgs))
            result = json.dumps({
                'count': len(data),
                'messages': data,
            }, )

        r = requests.post('http://hastebin.com/documents', data=result)
        r.raise_for_status()
        event.msg.reply('OK, archived {} messages at http://hastebin.com/{}'.format(size, r.json()['key']))

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
            fn.Count('*'), fn.Sum(fn.char_length(Message.content))
        ).tuples()[0]

        # Grab total custom emojis
        unique = list(Message.raw("""
            SELECT count(*) as c, regexp_matches(content, '<:(.+?):([0-9]+?)>', 'gi') as emoji
            FROM messages WHERE content ~* '<:.+:[0-9]+>' AND author_id=%s GROUP BY emoji
            ORDER BY c DESC;
        """, (user.id, )).tuples())

        custom = sum([i[0] for i in unique])

        deleted = base_query.where((Message.deleted >> True)).count()

        embed = MessageEmbed()
        embed.fields.append(
            MessageEmbedField(name='Total Messages', value=q[0], inline=True))
        embed.fields.append(
            MessageEmbedField(name='Total Characters', value=q[1], inline=True))
        embed.fields.append(
            MessageEmbedField(name='Deleted Messages', value=deleted, inline=True))
        embed.fields.append(
            MessageEmbedField(name='Total Custom Emoji', value=custom, inline=True))
        embed.fields.append(
            MessageEmbedField(name='Unique Emojis Used', value=len(unique), inline=True))
        embed.author = MessageEmbedAuthor(name=user.username, icon_url=user.avatar_url)
        embed.color = 0xF49AC2

        event.msg.reply('', embed=embed)

    @Plugin.command('emojistats', level=CommandLevels.MOD)
    def emojistats(self, event):
        q = list(Message.raw("""
            SELECT count(*) as c, regexp_matches(content, '<:(.+?):([0-9]+?)>', 'gi') as emoji
            FROM messages WHERE content ~* '<:.+:[0-9]+>' AND guild_id=%s GROUP BY emoji
            ORDER BY c DESC LIMIT 10;
        """, (event.guild.id, )).tuples())

        tbl = MessageTable()
        tbl.set_header('Count', 'Name', 'ID')

        for count, data in q:
            name, emoji_id = data
            tbl.add(count, name, emoji_id)

        event.msg.reply(tbl.compile())
