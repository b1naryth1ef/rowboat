import random
import requests
import humanize
import operator

from six import BytesIO
from PIL import Image
from peewee import fn
from gevent.pool import Pool
from datetime import datetime
from collections import defaultdict

from disco.types.user import GameType, Status
from disco.types.message import MessageEmbed
from disco.util.snowflake import to_datetime
from disco.util.sanitize import S

from rowboat.plugins import RowboatPlugin as Plugin, CommandFail
from rowboat.util.gevent import wait_many
from rowboat.util.stats import statsd, to_tags
from rowboat.types.plugin import PluginConfig
from rowboat.models.guild import GuildVoiceSession
from rowboat.models.user import User, Infraction
from rowboat.models.message import Message
from rowboat.util.images import get_dominant_colors_user, get_dominant_colors_guild
from rowboat.constants import (
    STATUS_EMOJI, EMOJI_RE, USER_MENTION_RE, CDN_URL
)


def get_status_emoji(presence):
    if presence.game and presence.game.type == GameType.STREAMING:
        return STATUS_EMOJI[GameType.STREAMING], 'Streaming'
    elif presence.status == Status.ONLINE:
        return STATUS_EMOJI[Status.ONLINE], 'Online'
    elif presence.status == Status.IDLE:
        return STATUS_EMOJI[Status.IDLE], 'Idle',
    elif presence.status == Status.DND:
        return STATUS_EMOJI[Status.DND], 'DND'
    elif presence.status in (Status.OFFLINE, Status.INVISIBLE):
        return STATUS_EMOJI[Status.OFFLINE], 'Offline'


def get_emoji_url(emoji):
    return CDN_URL.format('-'.join(
        char.encode("unicode_escape").decode("utf-8")[2:].lstrip("0")
        for char in emoji))


class UtilitiesConfig(PluginConfig):
    pass


@Plugin.with_config(UtilitiesConfig)
class UtilitiesPlugin(Plugin):
    @Plugin.command('coin', group='random', global_=True)
    def coin(self, event):
        """
        Flip a coin
        """
        event.msg.reply(random.choice(['heads', 'tails']))

    @Plugin.command('number', '[end:int] [start:int]', group='random', global_=True)
    def random_number(self, event, end=10, start=0):
        """
        Returns a random number
        """

        # Because someone will be an idiot
        if end > 9223372036854775807:
            return event.msg.reply(':warning: ending number too big!')

        if end <= start:
            return event.msg.reply(':warning: ending number must be larger than starting number!')

        event.msg.reply(str(random.randint(start, end)))

    @Plugin.command('cat', global_=True)
    def cat(self, event):
        # Sometimes random.cat gives us gifs (smh)
        for _ in range(3):
            try:
                r = requests.get('http://random.cat/meow')
                r.raise_for_status()
            except:
                continue

            url = r.json()['file']
            if not url.endswith('.gif'):
                break
        else:
            return event.msg.reply('404 cat not found :(')

        r = requests.get(url)
        r.raise_for_status()
        event.msg.reply('', attachments=[('cat.jpg', r.content)])

    @Plugin.command('emoji', '<emoji:str>', global_=True)
    def emoji(self, event, emoji):
        if not EMOJI_RE.match(emoji):
            return event.msg.reply(u'Unknown emoji: `{}`'.format(emoji))

        fields = []

        name, eid = EMOJI_RE.findall(emoji)[0]
        fields.append('**ID:** {}'.format(eid))
        fields.append('**Name:** {}'.format(S(name)))

        guild = self.state.guilds.find_one(lambda v: eid in v.emojis)
        if guild:
            fields.append('**Guild:** {} ({})'.format(S(guild.name), guild.id))

        url = 'https://discordapp.com/api/emojis/{}.png'.format(eid)
        r = requests.get(url)
        r.raise_for_status()
        return event.msg.reply('\n'.join(fields), attachments=[('emoji.png', r.content)])

    @Plugin.command('jumbo', '<emojis:str...>', global_=True)
    def jumbo(self, event, emojis):
        urls = []

        for emoji in emojis.split(' ')[:5]:
            if EMOJI_RE.match(emoji):
                _, eid = EMOJI_RE.findall(emoji)[0]
                urls.append('https://discordapp.com/api/emojis/{}.png'.format(eid))
            else:
                urls.append(get_emoji_url(emoji))

        width, height, images = 0, 0, []

        for r in Pool(6).imap(requests.get, urls):
            try:
                r.raise_for_status()
            except requests.HTTPError:
                return

            img = Image.open(BytesIO(r.content))
            height = img.height if img.height > height else height
            width += img.width + 10
            images.append(img)

        image = Image.new('RGBA', (width, height))
        width_offset = 0
        for img in images:
            image.paste(img, (width_offset, 0))
            width_offset += img.width + 10

        combined = BytesIO()
        image.save(combined, 'png', quality=55)
        combined.seek(0)
        return event.msg.reply('', attachments=[('emoji.png', combined)])

    @Plugin.command('seen', '<user:user>', global_=True)
    def seen(self, event, user):
        try:
            msg = Message.select(Message.timestamp).where(
                Message.author_id == user.id
            ).order_by(Message.timestamp.desc()).limit(1).get()
        except Message.DoesNotExist:
            return event.msg.reply(u"I've never seen {}".format(user))

        event.msg.reply(u'I last saw {} {} ago (at {})'.format(
            user,
            humanize.naturaldelta(datetime.utcnow() - msg.timestamp),
            msg.timestamp
        ))

    @Plugin.command('search', '<query:str...>', global_=True)
    def search(self, event, query):
        queries = []

        if query.isdigit():
            queries.append((User.user_id == query))

        q = USER_MENTION_RE.findall(query)
        if len(q) and q[0].isdigit():
            queries.append((User.user_id == q[0]))
        else:
            queries.append((User.username ** u'%{}%'.format(query.replace('%', ''))))

        if '#' in query:
            username, discrim = query.rsplit('#', 1)
            if discrim.isdigit():
                queries.append((
                    (User.username == username) &
                    (User.discriminator == int(discrim))))

        users = User.select().where(reduce(operator.or_, queries))
        if len(users) == 0:
            return event.msg.reply(u'No users found for query `{}`'.format(S(query, escape_codeblocks=True)))

        if len(users) == 1:
            if users[0].user_id in self.state.users:
                return self.info(event, self.state.users.get(users[0].user_id))

        return event.msg.reply(u'Found the following users for your query: ```{}```'.format(
            u'\n'.join(map(lambda i: u'{} ({})'.format(unicode(i), i.user_id), users[:25]))
        ))

    @Plugin.command('server', '[guild_id:snowflake]', global_=True)
    def server(self, event, guild_id=None):
        guild = self.state.guilds.get(guild_id) if guild_id else event.guild
        if not guild:
            raise CommandFail('invalid server')

        content = []
        content.append(u'**\u276F Server Information**')

        created_at = to_datetime(guild.id)
        content.append(u'Created: {} ago ({})'.format(
            humanize.naturaldelta(datetime.utcnow() - created_at),
            created_at.isoformat(),
        ))
        content.append(u'Members: {}'.format(len(guild.members)))
        content.append(u'Features: {}'.format(', '.join(guild.features) or 'none'))

        content.append(u'\n**\u276F Counts**')
        text_count = sum(1 for c in guild.channels.values() if not c.is_voice)
        voice_count = len(guild.channels) - text_count
        content.append(u'Roles: {}'.format(len(guild.roles)))
        content.append(u'Text: {}'.format(text_count))
        content.append(u'Voice: {}'.format(voice_count))

        content.append(u'\n**\u276F Members**')
        status_counts = defaultdict(int)
        for member in guild.members.values():
            if not member.user.presence:
                status = Status.OFFLINE
            else:
                status = member.user.presence.status
            status_counts[status] += 1

        for status, count in sorted(status_counts.items(), key=lambda i: str(i[0]), reverse=True):
            content.append(u'<{}> - {}'.format(
                STATUS_EMOJI[status], count
            ))

        embed = MessageEmbed()
        if guild.icon:
            embed.set_thumbnail(url=guild.icon_url)
            embed.color = get_dominant_colors_guild(guild)
        embed.description = '\n'.join(content)
        event.msg.reply('', embed=embed)

    @Plugin.command('info', '<user:user>')
    def info(self, event, user):
        content = []
        content.append(u'**\u276F User Information**')
        content.append(u'ID: {}'.format(user.id))
        content.append(u'Profile: <@{}>'.format(user.id))

        if user.presence:
            emoji, status = get_status_emoji(user.presence)
            content.append('Status: {} <{}>'.format(status, emoji))
            if user.presence.game and user.presence.game.name:
                if user.presence.game.type == GameType.DEFAULT:
                    content.append(u'Game: {}'.format(user.presence.game.name))
                else:
                    content.append(u'Stream: [{}]({})'.format(user.presence.game.name, user.presence.game.url))

        created_dt = to_datetime(user.id)
        content.append('Created: {} ago ({})'.format(
            humanize.naturaldelta(datetime.utcnow() - created_dt),
            created_dt.isoformat()
        ))

        member = event.guild.get_member(user.id) if event.guild else None
        if member:
            content.append(u'\n**\u276F Member Information**')

            if member.nick:
                content.append(u'Nickname: {}'.format(member.nick))

            content.append('Joined: {} ago ({})'.format(
                humanize.naturaldelta(datetime.utcnow() - member.joined_at),
                member.joined_at.isoformat(),
            ))

            if member.roles:
                content.append(u'Roles: {}'.format(
                    ', '.join((member.guild.roles.get(r).name for r in member.roles))
                ))

        # Execute a bunch of queries async
        newest_msg = Message.select(Message.timestamp).where(
            (Message.author_id == user.id) &
            (Message.guild_id == event.guild.id)
        ).limit(1).order_by(Message.timestamp.desc()).async()

        oldest_msg = Message.select(Message.timestamp).where(
            (Message.author_id == user.id) &
            (Message.guild_id == event.guild.id)
        ).limit(1).order_by(Message.timestamp.asc()).async()

        infractions = Infraction.select(
            Infraction.guild_id,
            fn.COUNT('*')
        ).where(
            (Infraction.user_id == user.id)
        ).group_by(Infraction.guild_id).tuples().async()

        voice = GuildVoiceSession.select(
            GuildVoiceSession.user_id,
            fn.COUNT('*'),
            fn.SUM(GuildVoiceSession.ended_at - GuildVoiceSession.started_at)
        ).where(
            (GuildVoiceSession.user_id == user.id) &
            (~(GuildVoiceSession.ended_at >> None))
        ).group_by(GuildVoiceSession.user_id).tuples().async()

        # Wait for them all to complete (we're still going to be as slow as the
        #  slowest query, so no need to be smart about this.)
        wait_many(newest_msg, oldest_msg, infractions, voice, timeout=10)
        tags = to_tags(guild_id=event.msg.guild.id)

        if newest_msg.value and oldest_msg.value:
            statsd.timing('sql.duration.newest_msg', newest_msg.value._query_time, tags=tags)
            statsd.timing('sql.duration.oldest_msg', oldest_msg.value._query_time, tags=tags)
            newest_msg = newest_msg.value.get()
            oldest_msg = oldest_msg.value.get()

            content.append(u'\n **\u276F Activity**')
            content.append('Last Message: {} ago ({})'.format(
                humanize.naturaldelta(datetime.utcnow() - newest_msg.timestamp),
                newest_msg.timestamp.isoformat(),
            ))
            content.append('First Message: {} ago ({})'.format(
                humanize.naturaldelta(datetime.utcnow() - oldest_msg.timestamp),
                oldest_msg.timestamp.isoformat(),
            ))

        if infractions.value:
            statsd.timing('sql.duration.infractions', infractions.value._query_time, tags=tags)
            infractions = list(infractions.value)
            total = sum(i[1] for i in infractions)
            content.append(u'\n**\u276F Infractions**')
            content.append('Total Infractions: {}'.format(total))
            content.append('Unique Servers: {}'.format(len(infractions)))

        if voice.value:
            statsd.timing('plugin.utilities.info.sql.voice', voice.value._query_time, tags=tags)
            voice = list(voice.value)
            content.append(u'\n**\u276F Voice**')
            content.append(u'Sessions: {}'.format(voice[0][1]))
            content.append(u'Time: {}'.format(humanize.naturaldelta(
                voice[0][2]
            )))

        embed = MessageEmbed()

        avatar = u'https://cdn.discordapp.com/avatars/{}/{}.png'.format(
            user.id,
            user.avatar,
        )

        embed.set_author(name=u'{}#{}'.format(
            user.username,
            user.discriminator,
        ), icon_url=avatar)

        embed.set_thumbnail(url=avatar)

        embed.description = '\n'.join(content)
        embed.color = get_dominant_colors_user(user, avatar)
        event.msg.reply('', embed=embed)
