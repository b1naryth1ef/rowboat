import re
import random
import requests
import humanize

from datetime import datetime
from emoji.unicode_codes import EMOJI_ALIAS_UNICODE

from rowboat import RowboatPlugin as Plugin
from rowboat.types.plugin import PluginConfig
from rowboat.plugins.messages import Message


CDN_URL = 'https://twemoji.maxcdn.com/2/72x72/{}.png'
EMOJI_RE = re.compile(r'<:(.+):([0-9]+)>')


def get_emoji_url(char):
    return CDN_URL.format(char.encode("unicode_escape").decode("utf-8")[2:].lstrip("0"))


class UtilitiesConfig(PluginConfig):
    pass


class UtilitiesPlugin(Plugin):
    @Plugin.command('coin')
    def coin(self, event):
        event.msg.reply(random.choice(['heads', 'tails']))

    @Plugin.command('cat')
    def cat(self, event):
        r = requests.get('http://random.cat/meow')
        r.raise_for_status()
        r = requests.get(r.json()['file'])
        r.raise_for_status()
        event.msg.reply('', attachment=('cat.jpg', r.content))

    @Plugin.command('urban', '<term:str...>')
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

    @Plugin.command('pwnd', '<email:str>')
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

    @Plugin.command('geoip', '<ip:str>')
    def geoip(self, event, ip):
        r = requests.get('http://json.geoiplookup.io/{}'.format(ip))
        r.raise_for_status()
        data = r.json()

        event.msg.reply('{} - {}, {} ({}) | {}, {}'.format(
            data['isp'],
            data['city'],
            data['region'],
            data['country_code'],
            data['latitude'],
            data['longitude'],
        ))

    @Plugin.command('emoji', '<emoji:str>')
    def emoji(self, event, emoji):
        if not EMOJI_RE.match(emoji):
            return event.msg.reply('Unknown emoji: `{}`'.format(emoji))

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

    @Plugin.command('jumbo', '<emoji:str>')
    def jumbo(self, event, emoji):
        if emoji in EMOJI_ALIAS_UNICODE.values():
            url = get_emoji_url(emoji)
        elif EMOJI_RE.match(emoji):
            _, eid = EMOJI_RE.findall(emoji)[0]
            url = 'https://discordapp.com/api/emojis/{}.png'.format(eid)
        else:
            return event.msg.reply('Invalid emoji: `{}`'.format(emoji))

        r = requests.get(url)
        r.raise_for_status()
        return event.msg.reply('', attachment=('emoji.png', r.content))

    @Plugin.command('seen', '<user:user>')
    def seen(self, event, user):
        try:
            msg = Message.select(Message.timestamp).where(
                Message.author_id == user.id
            ).order_by(Message.timestamp.desc()).limit(1).get()
        except Message.DoesNotExist:
            return event.msg.reply("I've never seen {}".format(user))

        event.msg.reply('I last saw {} {} ({})'.format(
            user,
            humanize.naturaltime(datetime.utcnow() - msg.timestamp),
            msg.timestamp
        ))
