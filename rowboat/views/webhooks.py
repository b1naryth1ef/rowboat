import json
import subprocess

from flask import Blueprint, request, current_app
from rowboat.redis import rdb
# from rowboat.util.decos import authed

from disco.types.message import MessageEmbed
from disco.types.webhook import Webhook

webhooks = Blueprint('webhooks', __name__, url_prefix='/webhooks')


@webhooks.route('/circle_ci', methods=['POST'])
def webhook_circle_ci():
    data = request.json['payload']

    embed = MessageEmbed()

    if data['outcome'] == 'success':
        embed.color = 0x42c88a
    else:
        embed.color = 0xed5c5c

    embed.title = 'Build #{} - {} ({})'.format(
        data['build_num'],
        data['subject'],
        data['author_name'],
    )

    embed.url = data['build_url']

    steps = []
    for step in data['steps']:
        emoji = ':x:' if any(True for act in step['actions'] if act.get('failed', False)) else ':white_check_mark:'
        steps.append('{} - {}'.format(
            emoji,
            step['name']
        ))

    embed.description = '\n'.join(steps)
    embed.description += '\n [View Diff]({})'.format(data['compare'])

    Webhook.execute_url(current_app.config.get('WEBHOOK_URL'), embeds=[embed])

    if data['outcome'] != 'success':
        return

    subprocess.Popen(['git', 'pull', 'origin', 'master']).wait()
    rdb.publish('actions', json.dumps({
        'type': 'RESTART',
    }))
    return '', 200
