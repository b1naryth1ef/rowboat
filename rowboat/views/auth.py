from flask import Blueprint, g, current_app, session, jsonify, redirect, request
from requests_oauthlib import OAuth2Session

from rowboat.models.user import User
from rowboat.util.decos import authed

auth = Blueprint('auth', __name__, url_prefix='/auth')


def token_updater(token):
    pass


def make_discord_session(token=None, state=None, scope=None):
    return OAuth2Session(
        client_id=current_app.config['DISCORD_CLIENT_ID'],
        token=token,
        state=state,
        scope=scope,
        redirect_uri=current_app.config['DISCORD_REDIRECT_URI'],
        auto_refresh_kwargs={
            'client_id': current_app.config['DISCORD_CLIENT_ID'],
            'client_secret': current_app.config['DISCORD_CLIENT_SECRET'],
        },
        auto_refresh_url=current_app.config['DISCORD_TOKEN_URL'],
        token_updater=token_updater)


@auth.route('/logout')
@authed
def auth_logout():
    g.user = None
    return jsonify({})


@auth.route('/discord')
def auth_discord():
    discord = make_discord_session(scope=('identify', ))
    auth_url, state = discord.authorization_url(current_app.config['DISCORD_AUTH_URL'])
    session['state'] = state
    return redirect(auth_url)


@auth.route('/discord/callback')
def auth_discord_callback():
    if request.values.get('error'):
        return request.values['error']

    if 'state' not in session:
        return 'no state', 400

    discord = make_discord_session(state=session['state'])
    token = discord.fetch_token(
        current_app.config['DISCORD_TOKEN_URL'],
        client_secret=current_app.config['DISCORD_CLIENT_SECRET'],
        authorization_response=request.url)

    discord = make_discord_session(token=token)

    data = discord.get(current_app.config['DISCORD_API_BASE_URL'] + '/users/@me').json()
    user = User.with_id(data['id'])

    if not user:
        return 'Unknown User', 403

    if not user.admin:
        return 'Invalid User', 403

    g.user = user

    return redirect('/')
