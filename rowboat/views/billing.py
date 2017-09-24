import hmac
import hashlib
import base64
from flask import Blueprint, request

from rowboat.config import fastspring
from rowboat.models.billing import Subscription

billing = Blueprint('billing', __name__, url_prefix='/api/billing')


def get_fastspring_payload():
    dec = hmac.new(fastspring['webhook_key'], request.data, hashlib.sha256)
    result = base64.b64encode(dec.digest())
    assert result == request.headers['X-FS-Signature']
    return request.json


@billing.route('/webhook/activate', methods=['POST'])
def billing_webhook_activate():
    for event in get_fastspring_payload()['events']:
        Subscription.activate(
            sub_id=event['data']['id'],
            user_id=int(event['data']['tags']['user_id']),
            guild_id=int(event['data']['tags']['guild_id']),
        )
    return 'OK', 200


@billing.route('/webhook/deactivate', methods=['POST'])
def billing_webhook_deactivate():
    for event in get_fastspring_payload()['events']:
        Subscription.get(sub_id=event['data']['id']).cancel('automatic - webhook', force=False)
    return 'OK', 200
