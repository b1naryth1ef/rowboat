from flask import Blueprint, request
# from rowboat.redis import rdb
# from rowboat.util.decos import authed

webhooks = Blueprint('webhooks', __name__, url_prefix='/webhooks')


@webhooks.route('/circle_ci')
def webhook_circle_ci():
    print request.json
    return '', 200
