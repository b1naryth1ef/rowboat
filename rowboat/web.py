import os
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

from flask import Flask, g, session
from holster.flask_ext import Holster

from rowboat.sql import init_db
from rowboat.models.user import User
from rowboat.models.notification import Notification

from yaml import load

rowboat = Holster(Flask(__name__))


@rowboat.app.before_first_request
def before_first_request():
    init_db()

    with open('config.yaml', 'r') as f:
        data = load(f)

    rowboat.app.secret_key = data.get('SECRET_KEY')
    rowboat.app.config.update(data['web'])


@rowboat.app.before_request
def check_auth():
    g.user = None

    if 'uid' in session:
        g.user = User.with_id(session['uid'])


@rowboat.app.after_request
def save_auth(response):
    if g.user and 'uid' not in session:
        session['uid'] = g.user.id
    elif not g.user and 'uid' in session:
        del session['uid']

    return response

@rowboat.app.context_processor
def inject_data():
    return dict(
        user=g.user,
        notifications=[i.to_user() for i in Notification.get_unreads()]
    )
