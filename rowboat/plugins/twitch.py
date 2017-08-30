import json
import requests

from holster.enum import Enum
from disco.types.message import MessageEmbed

from rowboat.plugins import RowboatPlugin as Plugin
from rowboat.types.plugin import PluginConfig
from rowboat.types import SlottedModel, DictField, Field, ChannelField, snowflake
from rowboat.redis import rdb

TWITCH_API_URL = 'https://api.twitch.tv/kraken'
TWITCH_USER_MAPPING_KEY = 't:um'

# Used to map what guilds listen to a given stream
TWITCH_STREAM_TO_GUILD_KEY = 't:stg:{}'

# Caches all streams a given guild listens too
TWITCH_STREAMS_GUILD_KEY = 't:s:{}'

# Caches state for a given stream
TWITCH_STREAM_STATE_KEY = 't:ss:{}'

# Caches state for a given guild
TWITCH_GUILD_STATE_KEY = 't:gs:{}:{}'

FormatMode = Enum(
    'PLAIN',
    'PRETTY',
)

NotificationType = Enum(
    'HERE',
    'EVERYONE',
    'ROLE'
)


class StreamConfig(SlottedModel):
    channel = Field(ChannelField)
    mode = Field(FormatMode, default=FormatMode.PRETTY)
    update = Field(bool, default=False)
    delete = Field(bool, default=False)

    notification_type = Field(NotificationType)
    notification_target = Field(snowflake)


class TwitchConfig(PluginConfig):
    streams = DictField(str, StreamConfig)


@Plugin.with_config(TwitchConfig)
class TwitchPlugin(Plugin):
    def load(self, ctx):
        super(TwitchPlugin, self).load(ctx)
        self.s = requests.Session()
        self.s.headers['Client-ID'] = 'sgoy3x5spnpsxs4opocpd4x5sn72b6'
        self.s.headers['Accept'] = 'application/vnd.twitchtv.v5+json'

        # Subscribe to configuration updates
        core_plugin = self.bot.plugins.get('CorePlugin')
        self._guild_config_update_listener = core_plugin.emitter.on(
            'GUILD_CONFIG_UPDATE',
            self.on_config_update
        )

    def unload(self, ctx):
        self._guild_config_update_listener.remove()
        super(TwitchPlugin, self).unload(ctx)

    def on_config_update(self, guild, config):
        if not hasattr(config.plugins, 'twitch'):
            return

        new_streams = set(config.plugins.twitch.streams.keys())
        old_streams = rdb.smembers(TWITCH_STREAMS_GUILD_KEY.format(guild.guild_id))

        with rdb.pipeline(transaction=False) as pipe:
            # Unlisten from these streams
            for stream in old_streams - new_streams:
                pipe.srem(TWITCH_STREAM_TO_GUILD_KEY.format(stream), str(guild.guild_id))

            for stream in new_streams:
                pipe.sadd(TWITCH_STREAM_TO_GUILD_KEY.format(stream), str(guild.guild_id))

            pipe.execute()

        with rdb.pipeline() as pipe:
            key = TWITCH_STREAMS_GUILD_KEY.format(guild.guild_id)
            pipe.delete(key)
            pipe.sadd(key, *new_streams)
            pipe.execute()

    def get_userid_for_usernames(self, usernames):
        result = {k: None for k in usernames}
        result.update(dict(zip(usernames, rdb.hmget(TWITCH_USER_MAPPING_KEY, usernames))))

        needed = [k for k, v in result.items() if not v]
        r = self.s.get(TWITCH_API_URL + '/users', params={
            'login': ','.join(needed),
        })

        try:
            r.raise_for_status()
        except:
            self.log.exception('Failed to map twitch userids: ')
            return result

        for user in r.json()['users']:
            result[user['name']] = user['_id']

        return result

    def get_channel_statuses(self, channel_ids):
        r = self.s.get(TWITCH_API_URL + '/streams/', params={
            'channel': ','.join(channel_ids),
        })
        r.raise_for_status()

        result = {cid: None for cid in channel_ids}
        for stream in r.json()['streams']:
            print stream
            result[stream['channel']['_id']] = stream

        return result

    def get_stream_state(self, channel_id):
        data = rdb.get(TWITCH_STREAM_STATE_KEY.format(channel_id))
        if not data:
            return {}
        return json.loads(data)

    def set_stream_state(self, channel_id, data):
        rdb.set(TWITCH_STREAM_STATE_KEY.format(channel_id), json.dumps(data))

    def prepare_state(self, stream):
        return {
            'name': stream['channel']['name'],
            'game': stream['game'],
            'type': stream['stream_type'],
            'viewers': stream['viewers'],
            'status': stream['channel']['status'],
            'preview': stream['preview']['large'],
        }

    @Plugin.schedule(10, init=False)
    def check_streams(self):
        # TODO: batch this at some point
        streams = rdb.sinter(*[TWITCH_STREAMS_GUILD_KEY.format(i) for i in self.state.guilds.keys()])
        if not streams:
            self.log.info('no streams to update')
            return

        mapping = {k: v for k, v in self.get_userid_for_usernames(streams).items() if v}
        self.log.info('Syncing stream infromation for: %s', mapping)

        statuses = self.get_channel_statuses(mapping.values())
        for channel_id, stream in statuses.iteritems():
            if not stream:
                continue

            old_state = self.get_stream_state(channel_id)
            new_state = self.prepare_state(stream) if stream else None

            self.on_stream_update(channel_id, old_state, new_state)
            continue

            # If we have a previous state, but we no longer have a state,
            #  we should consider this stream as moving from online to offline
            if old_state and not new_state:
                self.on_stream_offline(channel_id, old_state)
            # Otherwise if we have no previous state, but we have a stream,
            #  this stream is now going online
            elif new_state and not old_state:
                self.on_stream_online(channel_id, new_state)
            # If we have both a previous state and a stream, the stream is being
            #  updated
            elif new_state and old_state:
                self.on_stream_update(channel_id, old_state, new_state)

    def on_stream_update(self, channel_id, old_state, new_state):
        self.log.info('Updating %s / %s / %s', channel_id, old_state, new_state)
        name = new_state['name'] if new_state else old_state['name']

        guild_ids = rdb.smembers(TWITCH_STREAM_TO_GUILD_KEY.format(
            name,
        ))

        for guild_id in guild_ids:
            guild_state = rdb.get(TWITCH_GUILD_STATE_KEY.format(
                guild_id,
                channel_id,
            ))
            guild_state = json.loads(guild_state) if guild_state else {}

            # If there is no previous state, we should post online messages
            if not guild_state and new_state:
                config = self.call('CorePlugin.get_config', int(guild_id))
                twitch = getattr(config.plugins, 'twitch', None)

                if not twitch:
                    continue

                twitch = twitch.streams.get(name)

                message = self.post_message(guild_id, twitch, new_state)
                rdb.set(TWITCH_GUILD_STATE_KEY.format(
                    guild_id,
                    channel_id
                ), json.dumps({
                    'message': message.id,
                }))

            # Going offline
            if guild_state and not new_state:
                if twitch.delete:
                    channel = self.state.channels.get(twitch.channel)
                    channel.delete_message(guild_state['message'])

                rdb.delete(TWITCH_GUILD_STATE_KEY.format(guild_id, channel_id))

            print guild_state

        # Update stream state in redis
        self.set_stream_state(channel_id, new_state)

    def post_message(self, guild_id, twitch, new_state):
        embed = None
        # if twitch.mode == FormatMode.PRETTY:
        if True:
            embed = MessageEmbed()
            embed.title = u'{}'.format(new_state['status'])
            embed.url = 'https://twitch.tv/{}'.format(new_state['name'])
            embed.color = 0x6441A4
            embed.set_image(url=new_state['preview'])
            embed.add_field(name='Viewers', value=new_state['viewers'])

        if twitch.notification_type == NotificationType.HERE:
            msg = u'@here {} is now live!'.format(new_state['name'])
        elif twitch.notification_type == NotificationType.EVERYONE:
            msg = u'@everyone {} is now live!'.format(new_state['name'])
        elif twitch.notification_type == NotificationType.ROLE:
            msg = u'<@&{}> {} is now live!'.format(twitch.notification_target, new_state['name'])
        else:
            msg = u'{} is now live!'.format(new_state['name'])

        channel = self.state.channels.get(twitch.channel)
        return channel.send_message(msg, embed=embed)

# THONKS
#  - changing config with active stream

# Notify users
#  - at-everyone
#  - at-here
#  - at-role
# Live update information
