import json
import yaml
from flask import Blueprint, render_template, request, jsonify
from peewee import fn, SQL
from datetime import datetime, timedelta

from rowboat.redis import rdb
from rowboat.util.decos import authed
from rowboat.types.guild import GuildConfig
from rowboat.models.guild import Guild
from rowboat.models.message import Message


guilds = Blueprint('guilds', __name__, url_prefix='/guilds')


@guilds.route('/')
@authed
def guilds_list():
    return render_template(
        'guilds.html',
        guilds=Guild.select().where(Guild.enabled == 1))


@guilds.route('/<gid>')
@authed
def guild_info(gid):
    try:
        guild = Guild.get(Guild.guild_id == gid)
    except Guild.DoesNotExist:
        return 'Invalid Guild', 404

    return render_template('guild_info.html', guild=guild)


@guilds.route('/<gid>/config')
@authed
def guild_config(gid):
    try:
        guild = Guild.get(Guild.guild_id == gid)
    except Guild.DoesNotExist:
        return 'Invalid Guild', 404

    return render_template('guild_config.html', guild=guild)


@guilds.route('/<gid>/config/update', methods=['POST'])
@authed
def guild_config_update(gid):
    try:
        guild = Guild.get(Guild.guild_id == gid)

        data = yaml.load(request.values.get('data'))

        gc = GuildConfig(data)
        gc.validate()

        guild.config_raw = request.values.get('data')
        guild.config = data
        guild.save()
        guild.emit_update()
        return '', 200
    except Guild.DoesNotExist:
        return 'Invalid Guild', 404


@guilds.route('/<gid>/config/raw')
@authed
def guild_config_raw(gid):
    try:
        guild = Guild.get(Guild.guild_id == gid)
    except Guild.DoesNotExist:
        return 'Invalid Guild', 404

    return str(guild.config_raw) if guild.config_raw else yaml.safe_dump(guild.config)


@guilds.route('/<gid>/stats')
@authed
def guild_stats(gid):
    try:
        guild = Guild.get(Guild.guild_id == gid)
    except Guild.DoesNotExist:
        return 'Invalid Guild', 404

    key = 'web:stats:guild:{}'.format(gid)
    data = json.loads((rdb.get(key) or '{}'))

    if not data:
        data['messages'] = [{'date': int(i[0].strftime('%s')), 'value': i[1]} for i in Message.select(
            fn.date_trunc('hour', Message.timestamp).alias('ts'),
            fn.count('*')
        ).where(
            (Message.guild_id == guild.guild_id) &
            (Message.timestamp > (datetime.utcnow() - timedelta(days=7)))
        ).group_by(fn.date_trunc('hour', Message.timestamp)).order_by(SQL('ts').asc()).tuples()]

        # rdb.setex(key, json.dumps(data), 600)

    return jsonify(data)
