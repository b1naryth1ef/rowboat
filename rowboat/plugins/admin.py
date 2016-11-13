import six
import json
import requests

from disco.bot import CommandLevels

from rowboat import RowboatPlugin as Plugin
from rowboat.types.plugin import PluginConfig
from rowboat.plugins.messages import Message, MessageAuthor


class AdminConfig(PluginConfig):
    pass


class AdminPlugin(Plugin):
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
        ).join(MessageAuthor).order_by(Message.timestamp.desc()).limit(size)))

        if fmt == 'txt':
            data = map(encode_txt, msgs)
            result = '\n'.join(data)
        elif fmt == 'csv':
            data = map(encode_csv, msgs)
            data = ['id,timestamp,author_id,author,content'] + data
            result = '\n'.join(data)
        elif fmt == 'json':
            data = list(map(encode_json, msgs))
            result = json.dumps({
                'count': len(data),
                'messages': data,
            }, )

        r = requests.post('http://hastebin.com/documents', data=result)
        r.raise_for_status()
        event.msg.reply('OK, archived {} messages at http://hastebin.com/{}'.format(size, r.json()['key']))

    @Plugin.command('clean', '[size:int]', level=CommandLevels.MOD)
    def clean(self, event, size=25, typ=None):
        # TODO: global mutex
        if 0 > size >= 5000:
            return event.msg.reply(':warning: Too many messages, must be between 1-5000')

        msgs = list(reversed(Message.select().where(
            (Message.deleted >> False) &
            (Message.channel_id == event.channel.id)
        ).join(MessageAuthor).order_by(Message.timestamp.desc()).limit(size)))

        event.channel.delete_messages(msgs)
        event.msg.reply(':wastebasket: Ok, deleted {} messages'.format(len(msgs))).after(5).delete()

    @Plugin.command('msgstats', '<user:user>', level=CommandLevels.MOD)
    def stats(self, event, user):
        msgs = Message.select().where(
            (Message.author_id == user.id)
        ).count()

        deleted = Message.select().where(
            (Message.author_id == user.id) &
            (Message.deleted >> True)).count()

        event.msg.reply('{} has sent a total of {} messages, {} of which are deleted'.format(user, msgs, deleted))
