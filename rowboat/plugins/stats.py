import os
import psycopg2

from collections import defaultdict
from gevent.lock import Semaphore
from datetime import datetime
from disco.types.user import Status

from rowboat.plugins import BasePlugin as Plugin


GUILD_SNAPSHOT_SQL = '''
INSERT INTO guild_snapshots(
  time, guild_id,
  members, members_online, members_offline,
  members_away, members_dnd, members_voice,
  emojis
) VALUES (
    %(time)s, %(guild_id)s,
    %(members)s, %(online)s, %(offline)s,
    %(away)s, %(dnd)s, %(voice)s,
    %(emojis)s
);
'''

CHANNEL_MESSAGES_SNAPSHOT_SQL = '''
INSERT INTO channel_messages_snapshot VALUES (
    %(time)s, %(channel_id)s,
    %(created)s, %(updated)s, %(deleted)s,
    %(mentions)s, %(users)s
);
'''

ROLLUP_SQL = '''
WITH deleted AS (
  DELETE FROM {table} WHERE time < (NOW() - INTERVAL '{interval}')
  RETURNING *
)
INSERT INTO {table} SELECT
  date_trunc('{unit}', time), {pk}, {agg_keys}
FROM deleted GROUP BY date_trunc('{unit}', time), {pk};
'''

# Store 5 minutes for 7 days
# Store 1 hour for 12 months


class Rollup(object):
    def __init__(self, table, **kwargs):
        self.table = table
        self.kwargs = kwargs
        self.rollups = []

    def compile(self, args):
        keys = []
        for key in args['keys']:
            if len(key) == 2:
                func = key[1]
            else:
                func = args['default_agg']

            keys.append('{}({})'.format(func, key[0]))

        return ROLLUP_SQL.format(
            table=self.table,
            interval=args['interval'],
            unit=args['unit'],
            pk=args['pk'],
            agg_keys=', '.join(keys)
        )

    def add(self, **kwargs):
        kwargs.update(self.kwargs)
        self.rollups.append(kwargs)

    def run(self):
        for rollup in self.rollups:
            yield self.compile(rollup)


class StatsPlugin(Plugin):
    global_plugin = True

    def load(self, ctx):
        print 'LOAD'
        super(StatsPlugin, self).load(ctx)
        self.conn = psycopg2.connect(dbname='rowboat_stats', user='rowboat', port=int(os.getenv('PG_PORT', 5432)))

        self.guild_snapshot_rollup = Rollup('guild_snapshots', pk='guild_id', default_agg='avg', keys=[
            ('members', ), ('members_online', ), ('members_offline', ), ('members_away', ),
            ('members_dnd', ), ('members_voice', ), ('emojis', 'max')
        ])
        self.guild_snapshot_rollup.add(unit='minute', interval='15 minutes')
        self.guild_snapshot_rollup.add(unit='hour', interval='7 days')
        self.guild_snapshot_rollup.add(unit='day', interval='6 months')

        self.channel_messages_rollup = Rollup('channel_messages_snapshot', pk='channel_id', default_agg='sum', keys=[
            ('created', ), ('updated', ), ('deleted', ), ('mentions', ), ('users', )
        ])
        self.channel_messages_rollup.add(unit='minute', interval='15 minutes')
        self.channel_messages_rollup.add(unit='hour', interval='7 days')
        self.channel_messages_rollup.add(unit='day', interval='6 months')

        self.message_stats_lock = Semaphore()
        self.message_stats = defaultdict(lambda: defaultdict(int))
        self.hourly()

    def unload(self, ctx):
        print 'UNLOAD'
        super(StatsPlugin, self).unload(ctx)
        self.conn.close()

    @Plugin.listen('MessageCreate')
    def on_message_create(self, event):
        self.message_stats[event.channel_id].setdefault('channel_id', event.channel_id)
        self.message_stats[event.channel_id]['created'] += 1
        self.message_stats[event.channel_id]['mentions'] += len(event.mentions)

    @Plugin.listen('MessageDelete')
    def on_message_delete(self, event):
        self.message_stats[event.channel_id].setdefault('channel_id', event.channel_id)
        self.message_stats[event.channel_id]['deleted'] += 1

    @Plugin.listen('MessageUpdate')
    def on_message_update(self, event):
        self.message_stats[event.channel_id].setdefault('channel_id', event.channel_id)
        self.message_stats[event.channel_id]['updated'] += 1

    @Plugin.schedule(60 * 60, init=False)
    def hourly(self):
        with self.conn.cursor() as c:
            for query in self.guild_snapshot_rollup.run():
                c.execute(query)
            for query in self.channel_messages_rollup.run():
                c.execute(query)
            self.conn.commit()

    @Plugin.schedule(60, init=False)
    def minutely(self):
        for guild in list(self.state.guilds.values()):
            self.snapshot_guild(guild)

        with self.message_stats_lock:
            with self.conn.cursor() as c:
                for k in self.message_stats.keys():
                    self.message_stats[k]['time'] = datetime.utcnow()
                c.executemany(CHANNEL_MESSAGES_SNAPSHOT_SQL, self.message_stats.values())
                self.conn.commit()
            self.message_stats = defaultdict(lambda: defaultdict(int))

    def snapshot_guild(self, guild):
        args = {
            'online': 0,
            'offline': 0,
            'away': 0,
            'dnd': 0,
            'members': len(guild.members),
            'time': datetime.utcnow(),
            'guild_id': guild.id,
        }

        for member in guild.members.values():
            p = member.user.presence
            if not p:
                continue

            if p.status == Status.ONLINE:
                args['online'] += 1
            elif p.status == Status.OFFLINE:
                args['offline'] += 1
            elif p.status == Status.IDLE:
                args['away'] += 1
            elif p.status == Status.DND:
                args['dnd'] += 1

        args['voice'] = len(guild.voice_states)
        args['emojis'] = len(guild.emojis)

        with self.conn.cursor() as c:
            c.execute(GUILD_SNAPSHOT_SQL, args)
            self.conn.commit()
