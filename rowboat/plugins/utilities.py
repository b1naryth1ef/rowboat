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
from disco.types.message import MessageEmbed, MessageEmbedField, MessageEmbedThumbnail
from disco.util.snowflake import to_datetime

from rowboat.plugins import RowboatPlugin as Plugin
from rowboat.util import C
from rowboat.util.timing import Eventual
from rowboat.util.input import parse_duration
from rowboat.types.plugin import PluginConfig
from rowboat.models.user import User, Infraction
from rowboat.models.message import Message, Reminder
from rowboat.util.images import get_dominant_colors_user


YEAR_IN_SEC = 60 * 60 * 24 * 365
CDN_URL = 'https://twemoji.maxcdn.com/2/72x72/{}.png'
EMOJI_RE = re.compile(r'<:(.+):([0-9]+)>')
USER_MENTION_RE = re.compile('<@!?([0-9]+)>')


def get_emoji_url(emoji):
    return CDN_URL.format('-'.join(
        char.encode("unicode_escape").decode("utf-8")[2:].lstrip("0")
        for char in emoji))


class UtilitiesConfig(PluginConfig):
    pass


@Plugin.with_config(UtilitiesConfig)
class UtilitiesPlugin(Plugin):
    def load(self, ctx):
        super(UtilitiesPlugin, self).load(ctx)
        self.reminder_task = Eventual(self.trigger_reminders)
        self.spawn_later(10, self.queue_reminders)

    def queue_reminders(self):
        try:
            next_reminder = Reminder.select().order_by(
                Reminder.remind_at.asc()
            ).limit(1).get()
        except Reminder.DoesNotExist:
            return

        self.reminder_task.set_next_schedule(next_reminder.remind_at)

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
            return event.msg.reply('Ending number too big!')

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
            humanize.naturaldelta(datetime.utcnow() - msg.timestamp),
            msg.timestamp
        ))

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
            MessageEmbedField(name='User', value='<@{}>'.format(user.user_id), inline=True))

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
                MessageEmbedField(name='Join Date', value=str(member.joined_at), inline=True))

        infractions = Infraction.select().where(Infraction.user_id == user.id).count()
        embed.fields.append(
            MessageEmbedField(name='Infractions', value=str(infractions), inline=True))

        if member:
            embed.fields.append(
                MessageEmbedField(name='Roles', value=', '.join(
                    (event.guild.roles.get(i).name for i in member.roles)) or 'no roles', inline=False))

        embed.color = get_dominant_colors_user(user, avatar)
        event.msg.reply('', embed=embed)

    def trigger_reminders(self):
        reminders = Reminder.with_message_join().where(
            (Reminder.remind_at < (datetime.utcnow() + timedelta(seconds=1)))
        )

        for reminder in reminders:
            message = reminder.message_id
            channel = self.state.channels.get(message.channel_id)
            if not channel:
                self.log.warning('Not triggering reminder, channel %s was not found!',
                    message.channel_id)
                continue

            channel.send_message(u'<@{}> you asked me {} ago to remind you about: {}'.format(
                message.author_id,
                humanize.naturaldelta(reminder.created_at - datetime.utcnow()),
                reminder.content
            ))

            reminder.delete_instance()

        self.queue_reminders()

    @Plugin.command('clear', group='r')
    def cmd_remind_clear(self, event):
        count = Reminder.delete_for_user(event.author.id)
        return event.msg.reply(':ok_hand: I cleared {} reminders for you'.format(count))

    @Plugin.command('add', '<duration:str> <content:str...>', group='r')
    def cmd_remind(self, event, duration, content):
        if Reminder.count_for_user(event.author.id) > 30:
            return event.msg.reply(':warning: you an only have 15 reminders going at once!')

        remind_at = parse_duration(duration)
        if remind_at > (datetime.utcnow() + timedelta(seconds=5 * YEAR_IN_SEC)):
            return event.msg.reply(':warning: thats too far in the future, I\'ll forget!')

        r = Reminder.create(
            message_id=event.msg.id,
            remind_at=remind_at,
            content=content
        )
        self.reminder_task.set_next_schedule(r.remind_at)
        event.msg.reply(':ok_hand: I\'ll remind you in {} ({})'.format(
            humanize.naturaldelta(r.remind_at - datetime.utcnow()),
            duration
        ))
