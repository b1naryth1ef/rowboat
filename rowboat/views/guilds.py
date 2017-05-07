import yaml
import functools

from flask import Blueprint, render_template, request, g, jsonify

from rowboat.sql import stats_database
from rowboat.util.decos import authed
from rowboat.models.guild import Guild
from rowboat.models.channel import Channel

guilds = Blueprint('guilds', __name__, url_prefix='/guilds')


def with_guild(f):
    @authed
    @functools.wraps(f)
    def func(*args, **kwargs):
        try:
            if g.user.admin:
                guild = Guild.get(Guild.guild_id == kwargs.pop('gid'))
                guild.role = 'admin'
            else:
                guild = Guild.select(
                    Guild,
                    Guild.config['web_admins'][str(g.user.user_id)].alias('role')
                ).where(
                    (Guild.guild_id == kwargs.pop('gid')) &
                    (~(Guild.config['web_admins'][str(g.user.user_id)] >> None))
                ).get()
            return f(guild, *args, **kwargs)
        except Guild.DoesNotExist:
            return 'Invalid Guild', 404
    return func


@guilds.route('/<gid>')
@with_guild
def guild_info(guild):
    return render_template('guild_info.html', guild=guild)


@guilds.route('/<gid>/config')
@with_guild
def guild_config(guild):
    return render_template('guild_config.html', guild=guild)


@guilds.route('/<gid>/config/update', methods=['POST'])
@with_guild
def guild_config_update(guild):
    if guild.role not in ['admin', 'editor']:
        return 'Missing Permissions', 403

    if guild.role != 'admin':
        try:
            data = yaml.load(request.values.get('data'))
        except:
            return 'Invalid YAML', 400

        before = sorted(guild.config.get('web_admins', []).items(), key=lambda i: i[0])
        after = sorted([(str(k), v) for k, v in data.get('web_admins', []).items()], key=lambda i: i[0])

        if before != after:
            return 'Cannot Alter Permissions', 403

    try:
        guild.update_config(g.user.user_id, request.values.get('data'))
        return '', 200
    except Guild.DoesNotExist:
        return 'Invalid Guild', 404


@guilds.route('/<gid>/config/raw')
@with_guild
def guild_config_raw(guild):
    return str(guild.config_raw) if guild.config_raw else yaml.safe_dump(guild.config)


@guilds.route('/<gid>/stats/messages')
@with_guild
def guild_stats_messages_new(guild):
    mode = {
        '15m': ('minute', '15 minutes'),
        '1h': ('minute', '1 hour'),
        '24h': ('hour', '24 hours'),
        '7d': ('hour', '7 days'),
        '30d': ('day', '30 days'),
    }.get(request.values.get('mode', '15m'))

    if not mode:
        return 'Invalid Mode', 400

    # TODO: control time frame
    # TODO: caching

    channels = [i[0] for i in Channel.select(Channel.channel_id).where(
        (Channel.guild_id == guild.guild_id) &
        (Channel.deleted == 0)
    ).tuples()]

    with stats_database.cursor() as c:
        c.execute('''
            SELECT extract(epoch from date_trunc('{}', time)),
                sum(created) as Created,
                sum(updated) as Updated,
                sum(deleted) as Deleted,
                sum(mentions) as Mentions
            FROM channel_messages_snapshot
            WHERE channel_id IN %s AND time > (NOW() AT TIME ZONE 'UTC') - INTERVAL '{}'
            GROUP BY 1
            ORDER BY 1 ASC
        '''.format(mode[0], mode[1]), (tuple(channels), ))

        data = c.fetchall()
        cols = [[desc[0]] for desc in c.description]

    for row in data:
        for a, b in enumerate(row):
            cols[a].append(b)

    return jsonify({'data': cols[1:]})
