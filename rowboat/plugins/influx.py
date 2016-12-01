from gevent.lock import Semaphore
from datetime import datetime
from influxdb import InfluxDBClient
from collections import Counter

from disco.types.user import GameType
from rowboat import RowboatPlugin as Plugin


class InfluxPlugin(Plugin):
    def load(self, ctx):
        super(InfluxPlugin, self).load(ctx)
        self.influx = InfluxDBClient(database='rowboat')
        self.influx.create_database('rowboat', )
        self.points_cache = []
        self.lock = Semaphore()

    def unload(self, ctx):
        self.flush_points()
        super(InfluxPlugin, self).unload(ctx)

    @Plugin.schedule(5, init=False)
    def flush_points(self):
        if not len(self.points_cache):
            return

        with self.lock:
            self.influx.write_points(self.points_cache)
            self.points_cache = []

    def write_point(self, measurement, tags, value=1):
        self.points_cache.append({
            'measurement': measurement,
            'tags': tags,
            'time': datetime.utcnow(),
            'fields': {
                'value': value,
            }
        })

    @Plugin.listen('MessageCreate')
    def on_message_create(self, event):
        tags = {
            'channel_id': event.channel_id,
            'author_id': event.author.id,
            'mention_count': len(event.mentions),
            'content_size': len(event.content or ''),
        }

        if event.guild:
            tags['guild_id'] = event.guild.id

        self.write_point('message.create', tags)

    @Plugin.listen('MessageUpdate')
    def on_message_update(self, event):
        tags = {
            'channel_id': event.channel_id,
            'author_id': event.author.id,
            'mention_count': len(event.mentions),
            'content_size': len(event.content or ''),
        }

        if event.guild:
            tags['guild_id'] = event.guild.id

        self.write_point('message.update', tags)

    @Plugin.listen('MessageDelete')
    def on_message_delete(self, event):
        tags = {
            'channel_id': event.channel_id,
        }

        self.write_point('message.delete', tags)

    @Plugin.listen('MessageReactionAdd')
    def on_message_reaction_add(self, event):
        self.write_point('message.reaction.add', {
            'channel_id': event.channel_id,
            'user_id': event.user_id,
            'emoji_id': event.emoji.id,
            'emoji_name': event.emoji.name,
        })

    @Plugin.listen('MessageReactionRemove')
    def on_message_reaction_remove(self, event):
        self.write_point('message.reaction.remove', {
            'channel_id': event.channel_id,
            'user_id': event.user_id,
            'emoji_id': event.emoji.id,
            'emoji_name': event.emoji.name,
        })

    @Plugin.listen('GuildMemberAdd')
    def on_guild_member_add(self, event):
        guild = self.state.guilds.get(event.guild_id)
        if not guild:
            return

        self.write_point('guild.members.count', {
            'guild_id': event.guild_id,
        }, len(guild.members))

    @Plugin.listen('GuildMemberRemove')
    def on_guild_member_remove(self, event):
        guild = self.state.guilds.get(event.guild_id)
        if not guild:
            return

        self.write_point('guild.members.count', {
            'guild_id': event.guild_id,
        }, len(guild.members))

    @Plugin.listen('GuildCreate')
    def on_guild_create(self, event):
        if event.guild.member_count:
            self.write_point('guild.members.count', {
                'guild_id': event.guild.id,
            }, event.guild.member_count)

    @Plugin.schedule(10, init=False)
    def on_presence_update(self):
        for guild in self.state.guilds.values():
            status = Counter()
            games = Counter()
            streaming = 0

            for member in guild.members.values():
                if member.user.presence:
                    status[member.user.presence.status.name] += 1

                if member.user.presence.game and member.user.presence.game.type:
                    if member.user.presence.game.type == GameType.DEFAULT:
                        games[member.user.presence.game.name] += 1
                    else:
                        streaming += 1

            for status, count in status.items():
                self.write_point('guild.members.status', {
                    'status': status,
                    'guild_id': guild.id,
                }, count)

            for game, count in games.items():
                self.write_point('guild.members.games', {
                    'game': game,
                    'guild_id': guild.id,
                }, count)

            self.write_point('guild.members.streaming', {
                'guild_id': guild.id,
            }, streaming)
