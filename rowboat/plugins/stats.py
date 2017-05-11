from gevent.lock import Semaphore
from datetime import datetime
from collections import defaultdict
from influxdb import InfluxDBClient

from rowboat.plugins import BasePlugin as Plugin, raven_client


class StatsPlugin(Plugin):
    global_plugin = True

    def load(self, ctx):
        super(StatsPlugin, self).load(ctx)
        self.influx = InfluxDBClient(database='rowboat')
        self.influx.create_database('rowboat', )
        self.points_cache = []
        self.lock = Semaphore()

    def unload(self, ctx):
        self.flush_points()
        super(StatsPlugin, self).unload(ctx)

    @Plugin.schedule(5, init=False)
    def flush_points(self):
        if not len(self.points_cache):
            return

        try:
            with self.lock:
                self.influx.write_points(self.points_cache)
                self.points_cache = []
        except:
            self.log.exception('Failed to write influx data: ')
            raven_client.captureException()

    def write_point(self, measurement, tags, value=1):
        self.points_cache.append({
            'measurement': measurement,
            'tags': tags,
            'time': datetime.utcnow().replace(microsecond=0),
            'fields': {
                'value': value,
            }
        })

    @Plugin.listen('')
    def on_gateway_event(self, event):
        metadata = {
            'event': event.__class__.__name__,
        }

        if hasattr(event, 'guild_id'):
            metadata['guild_id'] = event.guild_id
        elif hasattr(event, 'guild') and event.guild:
            metadata['guild_id'] = event.guild.id

        self.write_point('gateway.events.receive', metadata)

    @Plugin.schedule(120, init=False)
    def track_presence(self):
        for guild in self.state.guilds.values():
            member_status = defaultdict(int)
            for member in guild.members.values():
                if member.user.presence and member.user.presence.status:
                    member_status[member.user.presence.status] += 1

            for k, v in member_status.items():
                self.write_point('guild.presence.snapshot', {
                    'guild_id': guild.id,
                    'status': str(k).lower()
                }, v)

    @Plugin.listen('MessageCreate')
    def on_message_create(self, event):
        tags = {
            'channel_id': event.channel_id,
            'author_id': event.author.id,
        }

        if event.guild:
            tags['guild_id'] = event.guild.id

        self.write_point('guild.message.create', tags)

    @Plugin.listen('MessageUpdate')
    def on_message_update(self, event):
        tags = {
            'channel_id': event.channel_id,
            'author_id': event.author.id,
        }

        if event.guild:
            tags['guild_id'] = event.guild.id

        self.write_point('guild.message.update', tags)

    @Plugin.listen('MessageDelete')
    def on_message_delete(self, event):
        tags = {
            'channel_id': event.channel_id,
        }

        self.write_point('guild.message.delete', tags)

    @Plugin.listen('MessageReactionAdd')
    def on_message_reaction_add(self, event):
        self.write_point('guild.message.reaction.add', {
            'channel_id': event.channel_id,
            'user_id': event.user_id,
            'emoji_id': event.emoji.id,
            'emoji_name': event.emoji.name,
        })

    @Plugin.listen('MessageReactionRemove')
    def on_message_reaction_remove(self, event):
        self.write_point('guild.message.reaction.remove', {
            'channel_id': event.channel_id,
            'user_id': event.user_id,
            'emoji_id': event.emoji.id,
            'emoji_name': event.emoji.name,
        })
