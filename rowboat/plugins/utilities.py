import re
import random
import requests
import humanize
import operator

from six import BytesIO
from PIL import Image
from pyquery import PyQuery
from gevent.pool import Pool
from datetime import datetime, timedelta
from disco.types.message import MessageTable, MessageEmbed, MessageEmbedField, MessageEmbedThumbnail
from disco.util.snowflake import to_datetime

from disco.types.user import User as DiscoUser
from disco.types.guild import Guild as DiscoGuild
from disco.types.channel import Channel as DiscoChannel

from rowboat.plugins import RowboatPlugin as Plugin
from rowboat.util import C
from rowboat.types.plugin import PluginConfig
from rowboat.models.user import User, Infraction
from rowboat.models.message import Message
from rowboat.util.images import get_dominant_colors_user


CDN_URL = 'https://twemoji.maxcdn.com/2/72x72/{}.png'
EMOJI_RE = re.compile(r'<:(.+):([0-9]+)>')
USER_MENTION_RE = re.compile('<@!?([0-9]+)>')
URL_REGEX = re.compile(r'(https?://[^\s]+)')


def get_emoji_url(emoji):
    return CDN_URL.format('-'.join(
        char.encode("unicode_escape").decode("utf-8")[2:].lstrip("0")
        for char in emoji))


class UtilitiesConfig(PluginConfig):
    pass


@Plugin.with_config(UtilitiesConfig)
class UtilitiesPlugin(Plugin):
    @Plugin.command('coin', global_=True)
    def coin(self, event):
        event.msg.reply(random.choice(['heads', 'tails']))

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
        event.msg.reply('', attachment=('cat.jpg', r.content))

    @Plugin.command('urban', '<term:str...>', global_=True)
    def urban(self, event, term):
        r = requests.get('http://api.urbandictionary.com/v0/define', params={
            'term': term,
        })
        r.raise_for_status()
        data = r.json()

        if not len(data['list']):
            return event.msg.reply(':warning: no matches')

        event.msg.reply('{} - {}'.format(
            data['list'][0]['word'],
            data['list'][0]['definition'],
        ))

    @Plugin.command('pwnd', '<email:str>', global_=True)
    def pwnd(self, event, email):
        r = requests.get('https://haveibeenpwned.com/api/v2/breachedaccount/{}'.format(
            email
        ))

        if r.status_code == 404:
            return event.msg.reply(":white_check_mark: you haven't been pwnd yet, awesome!")

        r.raise_for_status()
        data = r.json()

        sites = []

        for idx, site in enumerate(data):
            sites.append('{} - {} ({})'.format(
                site['BreachDate'],
                site['Title'],
                site['Domain'],
            ))

        return event.msg.reply(":warning: You've been pwnd on {} sites:\n{}".format(
            len(sites),
            '\n'.join(sites),
        ))

    @Plugin.command('geoip', '<ip:str>', global_=True)
    def geoip(self, event, ip):
        r = requests.get('http://json.geoiplookup.io/{}'.format(ip))
        r.raise_for_status()
        data = r.json()

        event.msg.reply(u'{} - {}, {} ({}) | {}, {}'.format(
            data['isp'],
            data['city'],
            data['region'],
            data['country_code'],
            data['latitude'],
            data['longitude'],
        ))

    @Plugin.command('mentions', global_=True)
    def mentions(self, event):
        q = Message.select().where(
            (Message.deleted == True) &
            (Message.mentions.contains(str(event.author.id))) &
            (Message.timestamp > (datetime.utcnow() - timedelta(days=7)))
        ).limit(5)

        q = Message.raw('''
            SELECT * FROM messages
            WHERE deleted=true
            AND timestamp > %s
            AND mentions @> %s
            ORDER BY timestamp DESC
            LIMIT 5;
        ''', datetime.utcnow() - timedelta(days=7), str(event.author.id)).execute()

        if not len(q):
            return event.msg.reply('No recent mentions that have been deleted')

        return event.msg.reply(u'```{}```'.format(
            '\n'.join([u'[{}] {}: {}'.format(m.timestamp, m.author, m.content) for m in q])
        ))

    @Plugin.command('google', '<query:str...>', global_=True)
    def google(self, event, query):
        url = 'https://www.google.com/search?hl=en&q={}&btnG=Google+Search&tbs=0&safe=off&tbm='
        r = requests.get(url.format(query))
        pq = PyQuery(r.content)

        results = []
        for result in pq('.g'):
            try:
                url = result.getchildren()[0].getchildren()[0]
                txt = result.getchildren()[1].getchildren()[1].text_content()
                results.append({
                    'url': url.attrib['href'],
                    'title': url.text_content(),
                    'text': txt,
                })
            except:
                continue

        if not results:
            return event.msg.reply('No results found')

        embed = MessageEmbed()
        embed.title = results[0]['title']
        embed.url = results[0]['url'].split('q=', 1)[-1].split('&', 1)[0]
        embed.description = results[0]['text']
        return event.msg.reply('', embed=embed)

    @Plugin.command('emoji', '<emoji:str>', global_=True)
    def emoji(self, event, emoji):
        if not EMOJI_RE.match(emoji):
            return event.msg.reply(u'Unknown emoji: `{}`'.format(emoji))

        fields = []

        name, eid = EMOJI_RE.findall(emoji)[0]
        fields.append('**ID:** {}'.format(eid))
        fields.append('**Name:** {}'.format(name))

        guild = self.state.guilds.find_one(lambda v: eid in v.emojis)
        if guild:
            fields.append('**Guild:** {} ({})'.format(guild.name, guild.id))

        url = 'https://discordapp.com/api/emojis/{}.png'.format(eid)
        r = requests.get(url)
        r.raise_for_status()
        return event.msg.reply('\n'.join(fields), attachment=('emoji.png', r.content))

    @Plugin.command('jumbo', '<emojis:str...>')
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
        return event.msg.reply('', attachment=('emoji.png', combined))

    @Plugin.command('seen', '<user:user>', global_=True)
    def seen(self, event, user):
        try:
            msg = Message.select(Message.timestamp).where(
                Message.author_id == user.id
            ).order_by(Message.timestamp.desc()).limit(1).get()
        except Message.DoesNotExist:
            return event.msg.reply(u"I've never seen {}".format(user))

        event.msg.reply(u'I last saw {} {} ({})'.format(
            user,
            humanize.naturaltime(datetime.utcnow() - msg.timestamp),
            msg.timestamp
        ))

    @Plugin.command('jpeg', '<url:str>', global_=True)
    def jpeg(self, event, url):
        url = URL_REGEX.findall(url)

        if len(url) != 1:
            return event.msg.reply('Invalid image URL')
        url = url[0]

        if url[-1] == '>':
            url = url[:-1]

        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            img = Image.open(BytesIO(r.content))
        except:
            return event.msg.reply('Invalid image')

        output = BytesIO()
        img.save(output, 'jpeg', quality=1, subsampling=0)
        output.seek(0)
        event.msg.reply('', attachment=('image.jpg', output))

    @Plugin.command('info', '<query:str...>', context={'mode': 'default'}, global_=True)
    @Plugin.command('search', '<query:str...>', context={'mode': 'search'}, global_=True)
    def info(self, event, query, mode=None):
        queries = []

        if query.isdigit():
            queries.append((User.user_id == query))

        q = USER_MENTION_RE.findall(query)
        if len(q) and q[0].isdigit():
            queries.append((User.user_id == q[0]))
        elif mode == 'search':
            queries.append((User.username ** u'%{}%'.format(query.replace('%', ''))))
        else:
            queries.append((User.username ** query.replace('%', '')))

        if '#' in query:
            username, discrim = query.rsplit('#', 1)
            if discrim.isdigit():
                queries.append((
                    (User.username == username) &
                    (User.discriminator == int(discrim))))

        users = User.select().where(reduce(operator.or_, queries))
        if len(users) == 0:
            return event.msg.reply(u'No users found for query `{}`'.format(C(query)))
        elif len(users) > 1:
            return event.msg.reply(u'Found the following users for your query: ```{}```'.format(
                u'\n'.join(map(unicode, users))
            ))
        else:
            user = users[0]

        embed = MessageEmbed()

        avatar = u'https://discordapp.com/api/users/{}/avatars/{}.jpg'.format(
            user.user_id,
            user.avatar,
        )

        member = event.guild.get_member(user.user_id) if event.guild else None

        embed.thumbnail = MessageEmbedThumbnail(url=avatar)
        embed.fields.append(
            MessageEmbedField(name='Username', value=user.username, inline=True))

        if member:
            embed.fields.append(
                MessageEmbedField(name='Nickname',
                    value=member.nick if member.nick else '`No Nickname`', inline=True))

        embed.fields.append(
            MessageEmbedField(name='ID', value=str(user.user_id), inline=True))

        embed.fields.append(
            MessageEmbedField(name='Creation Date', value=str(to_datetime(user.user_id)), inline=True))

        if member:
            embed.fields.append(
                MessageEmbedField(name='Join Date', value=member.joined_at, inline=True))

        infractions = Infraction.select().where(Infraction.user_id == user.id).count()
        embed.fields.append(
            MessageEmbedField(name='Infractions', value=str(infractions), inline=True))

        if member:
            embed.fields.append(
                MessageEmbedField(name='Roles', value=', '.join(
                    (event.guild.roles.get(i).name for i in member.roles)) or 'no roles', inline=False))

        embed.color = get_dominant_colors_user(user, avatar)
        event.msg.reply('', embed=embed)

    @Plugin.command('words', '<target:user|channel|guild>')
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
