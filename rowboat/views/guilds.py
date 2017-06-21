import yaml
import operator
import functools

from flask import Blueprint, render_template, request, g, jsonify

from rowboat.util.decos import authed
from rowboat.models.guild import Guild, GuildConfigChange
from rowboat.models.user import User, Infraction

guilds = Blueprint('guilds', __name__)


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
                guild = Guild.get(Guild.guild_id == kwargs.pop('gid'))
                guild.role = 'admin'
            else:
                guild = Guild.select(
                    Guild,
                    Guild.config['web'][str(g.user.user_id)].alias('role')
                ).where(
                    (Guild.guild_id == kwargs.pop('gid')) &
                    (~(Guild.config['web'][str(g.user.user_id)] >> None))
                ).get()
            return f(guild, *args, **kwargs)
        except Guild.DoesNotExist:
            return 'Invalid Guild', 404
    return func


@guilds.route('/guilds/<gid>')
@with_guild
def guild_info(guild):
    return render_template('guild_info.html', guild=guild)


@guilds.route('/guilds/<gid>/config')
@with_guild
def guild_config(guild):
    return render_template('guild_config.html', guild=guild)


@guilds.route('/guilds/<gid>/infractions')
@with_guild
def guild_infractions(guild):
    return render_template('guild_infractions.html', guild=guild)


@guilds.route('/api/guilds/<gid>/config/history')
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


@guilds.route('/api/guilds/<gid>/infractions')
@with_guild
def guild_infractions_list(guild):
    user = User.alias()
    actor = User.alias()

    columns = [
        Infraction.id,
        Infraction.type_,
        user.user_id,
        user.username,
        actor.user_id,
        actor.username,
        Infraction.reason,
        Infraction.created_at,
        Infraction.expires_at,
    ]

    def serialize(inf):
        type_ = {i.index: i for i in Infraction.Types.attrs}[inf.type_]
        return {
            'id': inf.id,
            'user': serialize_user(inf.user),
            'actor': serialize_user(inf.actor),
            'type': str(type_),
            'reason': inf.reason,
            'metadata': inf.metadata,
            'expires_at': (inf.expires_at.isoformat() if inf.expires_at else None) if inf.active else 'Expired',
            'created_at': inf.created_at.isoformat() if inf.created_at else None
        }

    sort_order = []
    for idx in xrange(32):
        ch = 'order[{}][column]'.format(idx)
        if ch not in request.values:
            break

        cd = 'order[{}][dir]'.format(idx)
        column = columns[int(request.values.get(ch))]
        order = request.values.get(cd)

        if order == 'asc':
            column = column.asc()
        else:
            column = column.desc()

        sort_order.append(column)

    base_q = Infraction.select(
            Infraction,
            user,
            actor
    ).join(
        user, on=(Infraction.user_id == user.user_id).alias('user'),
    ).switch(Infraction).join(
        actor, on=(Infraction.actor_id == actor.user_id).alias('actor'),
    ).where(
        Infraction.guild_id == guild.guild_id
    ).order_by(*sort_order)

    search = request.values.get('search[value]')
    opts = []
    if search:
        opts.append(user.username ** u'%{}%'.format(search))
        opts.append(actor.username ** u'%{}%'.format(search))
        opts.append(Infraction.reason ** u'%{}%'.format(search))

        if search.isdigit():
            opts.append(user.user_id == int(search))
            opts.append(actor.user_id == int(search))
            opts.append(Infraction.id == int(search))

    if opts:
        filter_q = base_q.where(reduce(operator.or_, opts))
    else:
        filter_q = base_q

    final_q = filter_q.offset(
        int(request.values.get('start'))
    ).limit(
        int(request.values.get('length'))
    )

    return jsonify({
        'draw': int(request.values.get('draw')),
        'recordsTotal': base_q.count(),
        'recordsFiltered': filter_q.count(),
        'data': map(serialize, final_q),
    })


@guilds.route('/api/guilds/<gid>/config/update', methods=['POST'])
@with_guild
def guild_config_update(guild):
    if guild.role not in ['admin', 'editor']:
        return 'Missing Permissions', 403

    if guild.role != 'admin':
        try:
            data = yaml.load(request.values.get('data'))
        except:
            return 'Invalid YAML', 400

        before = sorted(guild.config.get('web', []).items(), key=lambda i: i[0])
        after = sorted([(str(k), v) for k, v in data.get('web', []).items()], key=lambda i: i[0])

        if before != after:
            return 'Cannot Alter Permissions', 403

    try:
        guild.update_config(g.user.user_id, request.values.get('data'))
        return '', 200
    except Guild.DoesNotExist:
        return 'Invalid Guild', 404
    except Exception as e:
        return 'Invalid Data: %s' % e, 400


@guilds.route('/api/guilds/<gid>/config/raw')
@with_guild
def guild_config_raw(guild):
    return str(guild.config_raw) if guild.config_raw else yaml.safe_dump(guild.config)
