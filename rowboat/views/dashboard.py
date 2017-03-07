import json
import yaml
from flask import Blueprint, render_template, jsonify, request, g

from rowboat.redis import rdb
from rowboat.util.decos import authed
from rowboat.types.guild import GuildConfig
from rowboat.models.notification import Notification
from rowboat.models.message import Message
from rowboat.models.guild import Guild
from rowboat.models.user import User
from rowboat.models.channel import Channel

dashboard = Blueprint('dash', __name__)


@dashboard.route('/')
def dash_index():
    if g.user:
        obj = json.loads(rdb.get('web:dashboard:stats') or '{}')

        if not obj or 'refresh' in request.args:
            obj['messages'] = Message.select().count()
            obj['guilds'] = Guild.select().count()
            obj['users'] = User.select().count()
            obj['channels'] = Channel.select().count()

            rdb.setex('web:dashboard:stats', json.dumps(obj), 300)

        return render_template(
            'dashboard.html',
            stats=obj,
            guilds=Guild.select().where(Guild.enabled == 1),
        )
    return render_template('login.html')


@dashboard.route('/config/<gid>')
@authed
def guild_config(gid):
    try:
        guild = Guild.get(Guild.guild_id == gid)
    except Guild.DoesNotExist:
        return 'Invalid Guild', 404

    return render_template('config.html', guild=guild)


@dashboard.route('/config/<gid>/update', methods=['POST'])
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


@dashboard.route('/config/<gid>/raw')
@authed
def guild_config_raw(gid):
    try:
        guild = Guild.get(Guild.guild_id == gid)
    except Guild.DoesNotExist:
        return 'Invalid Guild', 404

    return str(guild.config_raw) if guild.config_raw else yaml.safe_dump(guild.config)


@dashboard.route('/notification/ack/<id>', methods=['POST'])
@authed
def notification_ack(id):
    Notification.update(read=True).where(
        Notification.id == id
    ).execute()
    return jsonify({})
