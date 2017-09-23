from . import task, get_client
from rowboat.models.message import Message
from disco.types.channel import MessageIterator


@task(max_concurrent=1, max_queue_size=10, global_lock=lambda guild_id: guild_id)
def backfill_guild(task, guild_id):
    client = get_client()
    for channel in client.api.guilds_channels_list(guild_id).values():
        backfill_channel.queue(channel.id)


@task(max_concurrent=6, max_queue_size=500, global_lock=lambda channel_id: channel_id)
def backfill_channel(task, channel_id):
    client = get_client()
    channel = client.api.channels_get(channel_id)

    # Hack the state
    client.state.channels[channel.id] = channel
    if channel.guild_id:
        client.state.guilds[channel.guild_id] = client.api.guilds_get(channel.guild_id)

    scanned = 0
    inserted = 0

    msgs_iter = MessageIterator(client, channel, bulk=True, after=1, direction=MessageIterator.Direction.DOWN)
    for chunk in msgs_iter:
        if not chunk:
            break

        scanned += len(chunk)
        inserted += len(Message.from_disco_message_many(chunk, safe=True))

    task.log.info('Completed backfill on channel %s, %s scanned and %s inserted', channel_id, scanned, inserted)
