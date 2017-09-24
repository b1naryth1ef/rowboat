from flask import Blueprint, g, jsonify
from peewee import JOIN

from rowboat.models.guild import Guild
from rowboat.models.billing import Subscription
from rowboat.util.decos import authed

users = Blueprint('users', __name__, url_prefix='/api/users')


@users.route('/@me')
@authed
def users_me():
    return jsonify(g.user.serialize(us=True))


@users.route('/@me/guilds')
@authed
def users_me_guilds():
    if g.user.admin:
        guilds = list(Guild.select(
            Guild, Subscription
        ).join(
            Subscription, JOIN.LEFT_OUTER, on=(Guild.premium_sub_id == Subscription.sub_id).alias('subscription')
        ))
    else:
        guilds = list(Guild.select(
            Guild,
            Subscription,
            Guild.config['web'][str(g.user.user_id)].alias('role')
        ).join(
            Subscription, JOIN.LEFT_OUTER, on=(Guild.premium_sub_id == Subscription.sub_id).alias('subscription')
        ).where(
            (~(Guild.config['web'][str(g.user.user_id)] >> None))
        ))

    return jsonify([
        guild.serialize(premium_subscription=guild.subscription) for guild in guilds
    ])
