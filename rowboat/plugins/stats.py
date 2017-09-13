import time

from datadog import initialize, statsd
from collections import defaultdict
from disco.types.user import Status

from rowboat import ENV
from rowboat.plugins import BasePlugin as Plugin


def to_tags(obj):
    return [u'{}:{}'.format(k, v) for k, v in obj.items()]


class StatsPlugin(Plugin):
    global_plugin = True

    def load(self, ctx):
        super(StatsPlugin, self).load(ctx)
        if ENV == 'docker':
            initialize(statsd_host='statsd', statsd_port=8125)
        else:
            initialize(statsd_host='localhost', statsd_port=8125)

        self.nonce = 0
        self.nonces = {}
        self.unhooked_send_message = self.client.api.channels_messages_create
        self.client.api.channels_messages_create = self.send_message_hook

    def unload(self, ctx):
        self.client.api.channels_messages_create = self.unhooked_send_message
        super(StatsPlugin, self).unload(ctx)

    def send_message_hook(self, *args, **kwargs):
        self.nonce += 1
        kwargs['nonce'] = self.nonce
        self.nonces[self.nonce] = time.time()
        return self.unhooked_send_message(*args, **kwargs)

    @Plugin.listen('')
    def on_gateway_event(self, event):
        metadata = {
            'event': event.__class__.__name__,
        }

        if hasattr(event, 'guild_id'):
            metadata['guild_id'] = event.guild_id
        elif hasattr(event, 'guild') and event.guild:
            metadata['guild_id'] = event.guild.id

        statsd.increment('gateway.events.received', tags=to_tags(metadata))

    @Plugin.schedule(120, init=False)
    def track_state(self):
        # Track presence across all our guilds
        for guild in self.state.guilds.values():
            member_status = defaultdict(int)
            for member in guild.members.values():
                if member.user.presence and member.user.presence.status:
                    member_status[member.user.presence.status] += 1
                else:
                    member_status[Status.OFFLINE] += 1

            for k, v in member_status.items():
                statsd.gauge('guild.presence.{}'.format(str(k).lower()), v, tags=to_tags({'guild_id': guild.id}))

        # Track some information about discos internal state
        statsd.gauge('disco.state.dms', len(self.state.dms))
        statsd.gauge('disco.state.guilds', len(self.state.guilds))
        statsd.gauge('disco.state.channels', len(self.state.channels))
        statsd.gauge('disco.state.users', len(self.state.users))
        statsd.gauge('disco.state.voice_states', len(self.state.voice_states))

    @Plugin.listen('MessageCreate')
    def on_message_create(self, event):
        tags = {
            'channel_id': event.channel_id,
            'author_id': event.author.id,
        }

        if event.guild:
            tags['guild_id'] = event.guild.id

        if event.author.id == self.client.state.me.id:
            if event.nonce in self.nonces:
                statsd.timing(
                    'latency.message_send',
                    time.time() - self.nonces[event.nonce],
                    tags=to_tags(tags)
                )
                del self.nonces[event.nonce]

        statsd.increment('guild.messages.create', tags=to_tags(tags))

    @Plugin.listen('MessageUpdate')
    def on_message_update(self, event):
        tags = {
            'channel_id': event.channel_id,
            'author_id': event.author.id,
        }

        if event.guild:
            tags['guild_id'] = event.guild.id

        statsd.increment('guild.messages.update', tags=to_tags(tags))

    @Plugin.listen('MessageDelete')
    def on_message_delete(self, event):
        tags = {
            'channel_id': event.channel_id,
        }

        statsd.increment('guild.messages.delete', tags=to_tags(tags))

    @Plugin.listen('MessageReactionAdd')
    def on_message_reaction_add(self, event):
        statsd.increment('guild.messages.reactions.add', tags=to_tags({
            'channel_id': event.channel_id,
            'user_id': event.user_id,
            'emoji_id': event.emoji.id,
            'emoji_name': event.emoji.name,
        }))

    @Plugin.listen('MessageReactionRemove')
    def on_message_reaction_remove(self, event):
        statsd.increment('guild.messages.reactions.remove', tags=to_tags({
            'channel_id': event.channel_id,
            'user_id': event.user_id,
            'emoji_id': event.emoji.id,
            'emoji_name': event.emoji.name,
        }))
