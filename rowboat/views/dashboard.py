import json

from flask import Blueprint, render_template, jsonify, request, g, Response, make_response
from datetime import datetime

from rowboat.redis import rdb
from rowboat.util.decos import authed
from rowboat.models.notification import Notification
from rowboat.models.message import Message, MessageArchive
from rowboat.models.guild import Guild
from rowboat.models.user import User
from rowboat.models.channel import Channel

dashboard = Blueprint('dash', __name__)


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
        )
    return render_template('login.html')


@dashboard.route('/notification/ack/<id>', methods=['POST'])
@authed
def notification_ack(id):
    Notification.update(read=True).where(
        Notification.id == id
    ).execute()
    return jsonify({})


@dashboard.route("/notifications/realtime")
@authed
def subscribe():
    def thread():
        sub = rdb.pubsub()
        sub.subscribe('notifications')

        for item in sub.listen():
            if item['type'] != 'message':
                continue

            yield ServerSentEvent(item['data']).encode()

    return Response(thread(), mimetype="text/event-stream")


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
