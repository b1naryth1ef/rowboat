import yaml
import json
import functools
import operator

from peewee import JOIN
from flask import Blueprint, request, g, jsonify

from rowboat.util.decos import authed
from rowboat.models.guild import Guild, GuildConfigChange
from rowboat.models.user import User, Infraction
from rowboat.models.billing import Subscription

guilds = Blueprint('guilds', __name__, url_prefix='/api/guilds')


def serialize_user(u):
    return {
        'user_id': str(u.user_id),
        'username': u.username,
        'discriminator': u.discriminator,
    }


def with_guild(f):
    @authed
    @functools.wraps(f)
    def func(*args, **kwargs):
        try:
            if g.user.admin:
                guild = Guild.select(Guild, Subscription).join(
                    Subscription, JOIN.LEFT_OUTER, on=(
                        Guild.premium_sub_id == Subscription.sub_id
                    ).alias('subscription')
                ).where(Guild.guild_id == kwargs.pop('gid')).get()
                guild.role = 'admin'
            else:
                guild = Guild.select(
                    Guild,
                    Subscription,
                    Guild.config['web'][str(g.user.user_id)].alias('role')
                ).join(
                    Subscription, JOIN.LEFT_OUTER, on=(
                        Guild.premium_sub_id == Subscription.sub_id
                    ).alias('subscription')
                ).where(
                    (Guild.guild_id == kwargs.pop('gid')) &
                    (~(Guild.config['web'][str(g.user.user_id)] >> None))
                ).get()
            return f(guild, *args, **kwargs)
        except Guild.DoesNotExist:
            return 'Invalid Guild', 404
    return func


@guilds.route('/<gid>')
@with_guild
def guild_get(guild):
    return jsonify(guild.serialize(premium_subscription=guild.subscription))


@guilds.route('/<gid>/config')
@with_guild
def guild_config(guild):
    return jsonify({
        'contents': unicode(guild.config_raw) if guild.config_raw else yaml.safe_dump(guild.config),
    })


@guilds.route('/<gid>/config', methods=['POST'])
@with_guild
def guild_z_config_update(guild):
    if guild.role not in ['admin', 'editor']:
        return 'Missing Permissions', 403

    # Calculate users diff
    try:
        data = yaml.load(request.json['config'])
    except:
        return 'Invalid YAML', 400

    before = sorted(guild.config.get('web', {}).items(), key=lambda i: i[0])
    after = sorted([(str(k), v) for k, v in data.get('web', {}).items()], key=lambda i: i[0])

    if guild.role != 'admin' and before != after:
        return 'Invalid Access', 403

    role = data.get('web', {}).get(g.user.user_id) or data.get('web', {}).get(str(g.user.user_id))
    if guild.role != role and not g.user.admin:
        print g.user.admin
        return 'Cannot change your own permissions', 400

    try:
        guild.update_config(g.user.user_id, request.json['config'])
        return '', 200
    except Guild.DoesNotExist:
        return 'Invalid Guild', 404
    except Exception as e:
        return 'Invalid Data: %s' % e, 400


CAN_FILTER = ['id', 'user_id', 'actor_id', 'type', 'reason']
CAN_SORT = ['id', 'user_id', 'actor_id', 'created_at', 'expires_at', 'type']


@guilds.route('/<gid>/infractions')
@with_guild
def guild_infractions(guild):
    user = User.alias()
    actor = User.alias()

    page = int(request.values.get('page', 1))
    if page < 1:
        page = 1

    limit = int(request.values.get('limit', 1000))
    if limit < 1 or limit > 1000:
        limit = 1000

    q = Infraction.select(Infraction, user, actor).join(
        user,
        on=((Infraction.user_id == user.user_id).alias('user'))
    ).switch(Infraction).join(
        actor,
        on=((Infraction.actor_id == actor.user_id).alias('actor'))
    )

    queries = []
    if 'filtered' in request.values:
        filters = json.loads(request.values['filtered'])

        for f in filters:
            if f['id'] not in CAN_FILTER:
                continue

            if f['id'] == 'type':
                queries.append(Infraction.type_ == Infraction.Types.get(f['value']))
            elif f['id'] == 'reason':
                queries.append(Infraction.reason ** f['value'].lower())
            else:
                queries.append(getattr(Infraction, f['id']) == f['value'])

    if queries:
        q = q.where(
            (Infraction.guild_id == guild.guild_id) &
            reduce(operator.and_, queries)
        )
    else:
        q = q.where((Infraction.guild_id == guild.guild_id))

    sorted_fields = []
    if 'sorted' in request.values:
        sort = json.loads(request.values['sorted'])

        for s in sort:
            if s['id'] not in CAN_SORT:
                continue

            if s['desc']:
                sorted_fields.append(
                    getattr(Infraction, s['id']).desc()
                )
            else:
                sorted_fields.append(
                    getattr(Infraction, s['id'])
                )

    if sorted_fields:
        q = q.order_by(*sorted_fields)
    else:
        q = q.order_by(Infraction.id.desc())

    q = q.paginate(
        page,
        limit,
    )

    return jsonify([i.serialize(guild=guild, user=i.user, actor=i.actor) for i in q])


@guilds.route('/<gid>/config/history')
@with_guild
def guild_config_history(guild):
    def serialize(gcc):
        return {
            'user': serialize_user(gcc.user_id),
            'before': unicode(gcc.before_raw),
            'after': unicode(gcc.after_raw),
            'created_at': gcc.created_at.isoformat(),
        }

    q = GuildConfigChange.select(GuildConfigChange, User).join(
        User, on=(User.user_id == GuildConfigChange.user_id),
    ).where(GuildConfigChange.guild_id == guild.guild_id).order_by(
        GuildConfigChange.created_at.desc()
    ).paginate(int(request.values.get('page', 1)), 25)

    return jsonify(map(serialize, q))


@guilds.route('/<gid>/premium/cancel', methods=['POST'])
@with_guild
def guild_premium_cancel(guild):
    sub = Subscription.get(sub_id=guild.premium_sub_id)
    if sub.user_id != g.user.id and not g.user.admin:
        return 'Invalid User', 403

    sub.cancel('User Requested %s' % sub.user_id)
    return '', 204
