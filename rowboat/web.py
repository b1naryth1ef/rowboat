import os; os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

import logging

from flask import Flask, g, session
from holster.flask_ext import Holster

from rowboat import ENV
from rowboat.sql import init_db
from rowboat.models.user import User

from yaml import load

rowboat = Holster(Flask(__name__))
logging.getLogger('peewee').setLevel(logging.DEBUG)


@rowboat.app.before_first_request
def before_first_request():
    init_db(ENV)

    with open('config.yaml', 'r') as f:
        data = load(f)

    rowboat.app.token = data.get('token')
    rowboat.app.config.update(data['web'])
    rowboat.app.config['token'] = data.get('SECRET_KEY')


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
    )
