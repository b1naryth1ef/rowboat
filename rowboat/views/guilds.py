import time
import requests
import yaml
import functools

from flask import Blueprint, render_template, request, current_app, make_response, g

from rowboat.redis import rdb
from rowboat.util.decos import authed
from rowboat.models.guild import Guild

guilds = Blueprint('guilds', __name__, url_prefix='/guilds')


def with_guild(f):
    @authed
    @functools.wraps(f)
    def func(*args, **kwargs):
        try:
            guild = Guild.get(Guild.guild_id == args[0])
            f(guild, *args[1:], **kwargs)
        except Guild.DoesNotExist:
            return 'Invalid Guild', 404
    return func


@guilds.route('/')
@authed
def guilds_list():
    return render_template(
        'guilds.html',
        guilds=Guild.select().where(Guild.enabled == 1))


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
    try:
        guild.update_config(g.user.user_id, request.values.get('data'))
        return '', 200
    except Guild.DoesNotExist:
        return 'Invalid Guild', 404


@guilds.route('/<gid>/config/raw')
@with_guild
def guild_config_raw(guild):
    return str(guild.config_raw) if guild.config_raw else yaml.safe_dump(guild.config)


@guilds.route('/<gid>/stats/messages.png')
def guild_stats_messages(gid):
    key = 'web:graphs:guild_msgs:{}'.format(gid)
    if rdb.exists(key):
        data = rdb.get(key)
    else:
        r = requests.get('http://zek.hydr0.com:3000/render/dashboard-solo/db/events', params={
            'from': str(int((time.time() - 604800) * 1000)),
            'to': str(int((time.time() * 1000))),
            'var-event': 'MessageCreate',
            'var-guild_id': gid,
            'panelId': 1,
            'width': 1200,
        }, headers={
            'Authorization': 'Bearer {}'.format(current_app.config['GRAFANA_KEY'])
        })
        data = r.content
        rdb.setex(key, data, 30)

    res = make_response(data)
    res.headers['Content-Type'] = 'image/png'
    return res
