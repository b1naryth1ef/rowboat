import six
import json
import time
import requests
import yaml

from flask import Blueprint, render_template, request, current_app, make_response
from itsdangerous import Signer

from rowboat.redis import rdb
from rowboat.util.decos import authed
from rowboat.types.guild import GuildConfig
from rowboat.models.guild import Guild
from rowboat.models.message import Message
from rowboat.models.user import User

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


@guilds.route('/<gid>/archive/<aid>.<fmt>')
def guild_archive(gid, aid, fmt):
    try:
        Guild.get(Guild.guild_id == gid)
    except Guild.DoesNotExist:
        return 'Invalid Guild', 404

    if fmt not in ['json', 'csv', 'txt']:
        return 'Invalid Format', 400

    s = Signer(current_app.config['SIGNER_KEY'])

    try:
        data = s.unsign(aid)
    except:
        return 'Invalid Archive', 400

    message_q = Message.select().join(User).order_by(Message.id.desc()).where(
        (Message.id > data['start']) &
        (Message.id < data['end']) &
        (Message.channel_id == data['channel'])
    )

    def encode_txt(msg):
        return u'{m.timestamp} {m.author}: {m.content}'.format(m=msg)

    def encode_csv(msg):
        def wrap(i):
            return u'"{}"'.format(six.text_type(i).replace('"', '""'))

        return ','.join(map(wrap, [
            msg.id,
            msg.timestamp,
            msg.author.id,
            msg.author,
            msg.content,
            str(msg.deleted).lower(),
        ]))

    def encode_json(msg):
        return {
            'id': str(msg.id),
            'timestamp': str(msg.timestamp),
            'user_id': str(msg.author.id),
            'username': msg.author.username,
            'discriminator': msg.author.discriminator,
            'content': msg.content,
            'deleted': msg.deleted,
        }

    msgs = list(reversed(message_q))
    mime_type = None

    if fmt == 'txt':
        data = map(encode_txt, msgs)
        result = u'\n'.join(data)
        mime_type = 'text/plain'
    elif fmt == 'csv':
        data = map(encode_csv, msgs)
        data = ['id,timestamp,author_id,author,content,deleted'] + data
        result = u'\n'.join(data)
        mime_type = 'text/csv'
    elif fmt == 'json':
        data = list(map(encode_json, msgs))
        result = json.dumps({
            'count': len(data),
            'messages': data,
        })
        mime_type = 'application/json'

    res = make_response(result)
    res.headers['Content-Type'] = mime_type
    return res


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
