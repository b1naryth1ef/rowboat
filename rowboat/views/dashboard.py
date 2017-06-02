import json
import subprocess

from flask import Blueprint, render_template, request, g, make_response
from datetime import datetime

from rowboat.redis import rdb
from rowboat.models.message import Message, MessageArchive
from rowboat.models.guild import Guild
from rowboat.models.user import User
from rowboat.models.channel import Channel
from rowboat.util.decos import authed

dashboard = Blueprint('dash', __name__)


def pretty_number(i):
    if i > 1000000:
        return '%.2fm' % (i / 1000000.0)
    elif i > 10000:
        return '%.2fk' % (i / 1000.0)
    return str(i)


class ServerSentEvent(object):
    def __init__(self, data):
        self.data = data
        self.event = None
        self.id = None
        self.desc_map = {
            self.data: "data",
            self.event: "event",
            self.id: "id"
        }

    def encode(self):
        if not self.data:
            return ""
        lines = ["%s: %s" % (v, k) for k, v in self.desc_map.iteritems() if k]
        return "%s\n\n" % "\n".join(lines)


@dashboard.route('/')
def dash_index():
    if g.user:
        if g.user.admin:
            stats = json.loads(rdb.get('web:dashboard:stats') or '{}')

            if not stats or 'refresh' in request.args:
                stats['messages'] = pretty_number(Message.select().count())
                stats['guilds'] = pretty_number(Guild.select().count())
                stats['users'] = pretty_number(User.select().count())
                stats['channels'] = pretty_number(Channel.select().count())

                rdb.setex('web:dashboard:stats', json.dumps(stats), 300)

            guilds = Guild.select().order_by(Guild.guild_id)
        else:
            stats = {}
            guilds = Guild.select(
                Guild, Guild.config['web'][str(g.user.user_id)].alias('role')
            ).where(
                (Guild.enabled == 1) &
                (~(Guild.config['web'][str(g.user.user_id)] >> None))
            )

        return render_template(
            'dashboard.html',
            stats=stats,
            guilds=guilds,
        )
    return render_template('login.html')


@dashboard.route('/archive/<aid>.<fmt>')
def archive(aid, fmt):
    try:
        archive = MessageArchive.select().where(
            (MessageArchive.archive_id == aid) &
            (MessageArchive.expires_at > datetime.utcnow())
        ).get()
    except MessageArchive.DoesNotExist:
        return 'Invalid or Expires Archive ID', 404

    mime_type = None
    if fmt == 'json':
        mime_type == 'application/json'
    elif fmt == 'txt':
        mime_type = 'text/plain'
    elif fmt == 'csv':
        mime_type = 'text/csv'

    res = make_response(archive.encode(fmt))
    res.headers['Content-Type'] = mime_type
    return res


@dashboard.route('/api/deploy', methods=['POST'])
@authed
def deploy():
    if not g.user.admin:
        return '', 401

    subprocess.Popen(['git', 'pull', 'origin', 'master']).wait()
    rdb.publish('actions', json.dumps({
        'type': 'RESTART',
    }))
    return '', 200
