import csv
import gevent
import humanize

from StringIO import StringIO
from holster.emitter import Priority

from datetime import datetime

from disco.bot import CommandLevels
from disco.types.user import User as DiscoUser
from disco.types.message import MessageTable, MessageEmbed

from rowboat.plugins import RowboatPlugin as Plugin, CommandFail, CommandSuccess
from rowboat.util.timing import Eventual
from rowboat.util.input import parse_duration
from rowboat.types import Field, snowflake
from rowboat.types.plugin import PluginConfig
from rowboat.plugins.modlog import Actions
from rowboat.models.user import User, Infraction
from rowboat.models.guild import GuildMemberBackup, GuildBan
from rowboat.constants import (
    GREEN_TICK_EMOJI_ID, RED_TICK_EMOJI_ID, GREEN_TICK_EMOJI, RED_TICK_EMOJI
)


def clamp(string, size):
    if len(string) > size:
        return string[:size] + '...'
    return string


def maybe_string(obj, exists, notexists, **kwargs):
    if obj:
        return exists.format(o=obj, **kwargs)
    return notexists.format(**kwargs)


class InfractionsConfig(PluginConfig):
    # Whether to confirm actions in the channel they are executed
    confirm_actions = Field(bool, default=True)
    confirm_actions_reaction = Field(bool, default=False)
    confirm_actions_expiry = Field(int, default=0)

    # Whether to notify users on actions
    notify_actions = Field(bool, default=False)

    # The mute role
    mute_role = Field(snowflake, default=None)

    # Level required to edit reasons
    reason_edit_level = Field(int, default=int(CommandLevels.ADMIN))


@Plugin.with_config(InfractionsConfig)
class InfractionsPlugin(Plugin):
    def load(self, ctx):
        super(InfractionsPlugin, self).load(ctx)

        self.inf_task = Eventual(self.clear_infractions)
        self.spawn_later(5, self.queue_infractions)

    def queue_infractions(self):
        next_infraction = list(Infraction.select().where(
            (Infraction.active == 1) &
            (~(Infraction.expires_at >> None))
        ).order_by(Infraction.expires_at.asc()).limit(1))

        if not next_infraction:
            self.log.info('[INF] no infractions to wait for')
            return

        self.log.info('[INF] waiting until %s for %s', next_infraction[0].expires_at, next_infraction[0].id)
        self.inf_task.set_next_schedule(next_infraction[0].expires_at)

    def clear_infractions(self):
        expired = list(Infraction.select().where(
            (Infraction.active == 1) &
            (Infraction.expires_at < datetime.utcnow())
        ))

        self.log.info('[INF] attempting to clear %s expired infractions', len(expired))

        for item in expired:
            guild = self.state.guilds.get(item.guild_id)
            if not guild:
                self.log.warning('[INF] failed to clear infraction %s, no guild exists', item.id)
                continue

            # TODO: hacky
            type_ = {i.index: i for i in Infraction.Types.attrs}[item.type_]
            if type_ == Infraction.Types.TEMPBAN:
                self.call(
                    'ModLogPlugin.create_debounce',
                    guild.id,
                    ['GuildBanRemove'],
                    user_id=item.user_id,
                )

                guild.delete_ban(item.user_id)

                # TODO: perhaps join on users above and use username from db
                self.call(
                    'ModLogPlugin.log_action_ext',
                    Actions.MEMBER_TEMPBAN_EXPIRE,
                    guild.id,
                    user_id=item.user_id,
                    user=unicode(self.state.users.get(item.user_id) or item.user_id),
                    inf=item
                )
            elif type_ == Infraction.Types.TEMPMUTE or Infraction.Types.TEMPROLE:
                member = guild.get_member(item.user_id)
                if member:
                    if item.metadata['role'] in member.roles:
                        self.call(
                            'ModLogPlugin.create_debounce',
                            guild.id,
                            ['GuildMemberUpdate'],
                            user_id=item.user_id,
                            role_id=item.metadata['role'],
                        )

                        member.remove_role(item.metadata['role'])

                        self.call(
                            'ModLogPlugin.log_action_ext',
                            Actions.MEMBER_TEMPMUTE_EXPIRE,
                            guild.id,
                            member=member,
                            inf=item
                        )
                else:
                    GuildMemberBackup.remove_role(
                        item.guild_id,
                        item.user_id,
                        item.metadata['role'])
            else:
                self.log.warning('[INF] failed to clear infraction %s, type is invalid %s', item.id, item.type_)
                continue

            # TODO: n+1
            item.active = False
            item.save()

        # Wait a few seconds to backoff from a possible bad loop, and requeue new infractions
        gevent.sleep(5)
        self.queue_infractions()

    @Plugin.listen('GuildMemberUpdate', priority=Priority.BEFORE)
    def on_guild_member_update(self, event):
        pre_member = event.guild.members.get(event.id)
        if not pre_member:
            return

        pre_roles = set(pre_member.roles)
        post_roles = set(event.roles)
        if pre_roles == post_roles:
            return

        removed = pre_roles - post_roles

        # If the user was unmuted, mark any temp-mutes as inactive
        if event.config.mute_role in removed:
            Infraction.clear_active(event, event.user.id, [Infraction.Types.TEMPMUTE])

    @Plugin.listen('GuildBanRemove')
    def on_guild_ban_remove(self, event):
        Infraction.clear_active(event, event.user.id, [Infraction.Types.BAN, Infraction.Types.TEMPBAN])

    @Plugin.command('unban', '<user:snowflake> [reason:str...]', level=CommandLevels.MOD)
    def unban(self, event, user, reason=None):
        try:
            GuildBan.get(user_id=user, guild_id=event.guild.id)
            event.guild.delete_ban(user)
        except GuildBan.DoesNotExist:
            raise CommandFail('user with id `{}` is not banned'.format(user))

        Infraction.create(
            guild_id=event.guild.id,
            user_id=user,
            actor_id=event.author.id,
            type_=Infraction.Types.UNBAN,
            reason=reason
        )
        raise CommandSuccess('unbanned user with id `{}`'.format(user))

    @Plugin.command('archive', group='infractions', level=CommandLevels.ADMIN)
    def infractions_archive(self, event):
        user = User.alias()
        actor = User.alias()

        q = Infraction.select(Infraction, user, actor).join(
            user,
            on=((Infraction.user_id == user.user_id).alias('user'))
        ).switch(Infraction).join(
            actor,
            on=((Infraction.actor_id == actor.user_id).alias('actor'))
        ).where(Infraction.guild_id == event.guild.id)

        buff = StringIO()
        w = csv.writer(buff)

        for inf in q:
            w.writerow([
                inf.id,
                inf.user_id,
                unicode(inf.user).encode('utf-8'),
                inf.actor_id,
                unicode(inf.actor).encode('utf-8'),
                unicode({i.index: i for i in Infraction.Types.attrs}[inf.type_]).encode('utf-8'),
                unicode(inf.reason).encode('utf-8'),
            ])

        event.msg.reply('Ok, here is an archive of all infractions', attachments=[
            ('infractions.csv', buff.getvalue())
        ])

    @Plugin.command('info', '<infraction:int>', group='infractions', level=CommandLevels.MOD)
    def infraction_info(self, event, infraction):
        try:
            user = User.alias()
            actor = User.alias()

            infraction = Infraction.select(Infraction, user, actor).join(
                user,
                on=((Infraction.user_id == user.user_id).alias('user'))
            ).switch(Infraction).join(
                actor,
                on=((Infraction.actor_id == actor.user_id).alias('actor'))
            ).where(
                    (Infraction.id == infraction) &
                    (Infraction.guild_id == event.guild.id)
            ).get()
        except Infraction.DoesNotExist:
            raise CommandFail('cannot find an infraction with ID `{}`'.format(infraction))

        type_ = {i.index: i for i in Infraction.Types.attrs}[infraction.type_]
        embed = MessageEmbed()

        if type_ in (Infraction.Types.MUTE, Infraction.Types.TEMPMUTE, Infraction.Types.TEMPROLE):
            embed.color = 0xfdfd96
        elif type_ in (Infraction.Types.KICK, Infraction.Types.SOFTBAN):
            embed.color = 0xffb347
        else:
            embed.color = 0xff6961

        embed.title = str(type_).title()
        embed.set_thumbnail(url=infraction.user.get_avatar_url())
        embed.add_field(name='User', value=unicode(infraction.user), inline=True)
        embed.add_field(name='Moderator', value=unicode(infraction.actor), inline=True)
        embed.add_field(name='Active', value='yes' if infraction.active else 'no', inline=True)
        if infraction.active and infraction.expires_at:
            embed.add_field(name='Expires', value=humanize.naturaldelta(infraction.expires_at - datetime.utcnow()))
        embed.add_field(name='Reason', value=infraction.reason or '_No Reason Given', inline=False)
        embed.timestamp = infraction.created_at.isoformat()
        event.msg.reply('', embed=embed)

    @Plugin.command('search', '[query:user|str...]', group='infractions', level=CommandLevels.MOD)
    def infraction_search(self, event, query=None):
        q = (Infraction.guild_id == event.guild.id)

        if query and isinstance(query, list) and isinstance(query[0], DiscoUser):
            query = query[0].id
        elif query:
            query = ' '.join(query)

        if query and (isinstance(query, int) or query.isdigit()):
            q &= (
                (Infraction.id == int(query)) |
                (Infraction.user_id == int(query)) |
                (Infraction.actor_id == int(query)))
        elif query:
            q &= (Infraction.reason ** query)

        user = User.alias()
        actor = User.alias()

        infractions = Infraction.select(Infraction, user, actor).join(
            user,
            on=((Infraction.user_id == user.user_id).alias('user'))
        ).switch(Infraction).join(
            actor,
            on=((Infraction.actor_id == actor.user_id).alias('actor'))
        ).where(q).order_by(Infraction.created_at.desc()).limit(6)

        tbl = MessageTable()

        tbl.set_header('ID', 'Created', 'Type', 'User', 'Moderator', 'Active', 'Reason')
        for inf in infractions:
            type_ = {i.index: i for i in Infraction.Types.attrs}[inf.type_]
            reason = inf.reason or ''
            if len(reason) > 256:
                reason = reason[:256] + '...'

            if inf.active:
                active = 'yes'
                if inf.expires_at:
                    active += ' (expires in {})'.format(humanize.naturaldelta(inf.expires_at - datetime.utcnow()))
            else:
                active = 'no'

            tbl.add(
                inf.id,
                inf.created_at.isoformat(),
                str(type_),
                unicode(inf.user),
                unicode(inf.actor),
                active,
                clamp(reason, 128)
            )

        event.msg.reply(tbl.compile())

    @Plugin.command('recent', aliases=['latest'], group='infractions', level=CommandLevels.MOD)
    def infractions_recent(self, event):
        # TODO: fucking write this bruh
        pass

    @Plugin.command('duration', '<infraction:int> <duration:str>', group='infractions', level=CommandLevels.MOD)
    def infraction_duration(self, event, infraction, duration):
        try:
            inf = Infraction.get(id=infraction)
        except Infraction.DoesNotExist:
            raise CommandFail('invalid infraction (try `!infractions recent`)')

        if inf.actor_id != event.author.id and event.user_level < CommandLevels.ADMIN:
            raise CommandFail('only administrators can modify the duration of infractions created by other moderators')

        if not inf.active:
            raise CommandFail('that infraction is not active and cannot be updated')

        expires_dt = parse_duration(duration, inf.created_at)

        converted = False
        if inf.type_ in [Infraction.Types.MUTE.index, Infraction.Types.BAN.index]:
            inf.type_ = (
                Infraction.Types.TEMPMUTE
                if inf.type_ == Infraction.Types.MUTE.index else
                Infraction.Types.TEMPBAN
            )
            converted = True
        elif inf.type_ not in [
                Infraction.Types.TEMPMUTE.index,
                Infraction.Types.TEMPBAN.index,
                Infraction.Types.TEMPROLE.index]:
            raise CommandFail('cannot set the duration for that type of infraction')

        inf.expires_at = expires_dt
        inf.save()
        self.queue_infractions()

        if converted:
            raise CommandSuccess('ok, I\'ve made that infraction temporary, it will now expire on {}'.format(
                inf.expires_at.isoformat()
            ))
        else:
            raise CommandSuccess('ok, I\'ve updated that infractions duration, it will now expire on {}'.format(
                inf.expires_at.isoformat()
            ))

    @Plugin.command('reason', '<infraction:int> <reason:str...>', level=CommandLevels.MOD)
    def reason(self, event, infraction, reason):
        try:
            inf = Infraction.get(id=infraction)
        except Infraction.DoesNotExist:
            inf = None

        if inf is None or inf.guild_id != event.guild.id:
            event.msg.reply('Unknown infraction ID')
            return

        if not inf.actor_id:
            inf.actor_id = event.author.id

        if inf.actor_id != event.author.id and event.user_level < event.config.reason_edit_level:
            raise CommandFail('you do not have the permissions required to edit other moderators infractions')

        inf.reason = reason
        inf.save()

        raise CommandSuccess('I\'ve updated the reason for infraction #{}'.format(inf.id))

    def can_act_on(self, event, victim_id, throw=True):
        if event.author.id == victim_id:
            if not throw:
                return False
            raise CommandFail('cannot execute that action on yourself')

        victim_level = self.bot.plugins.get('CorePlugin').get_level(event.guild, victim_id)

        if event.user_level <= victim_level:
            if not throw:
                return False
            raise CommandFail('invalid permissions')

        return True

    def confirm_action(self, event, message):
        if not event.config.confirm_actions:
            return

        if event.config.confirm_actions_reaction:
            event.msg.add_reaction(GREEN_TICK_EMOJI)
            return

        msg = event.msg.reply(message)

        if event.config.confirm_actions_expiry > 0:
            # Close over this thread local
            expiry = event.config.confirm_actions_expiry

            def f():
                gevent.sleep(expiry)
                msg.delete()

            # Run this in a greenlet so we dont block event execution
            self.spawn(f)

    @Plugin.command('mute', '<user:user|snowflake> [reason:str...]', level=CommandLevels.MOD)
    @Plugin.command('tempmute', '<user:user|snowflake> <duration:str> [reason:str...]', level=CommandLevels.MOD)
    def tempmute(self, event, user, duration=None, reason=None):
        if not duration and reason:
            duration = parse_duration(reason.split(' ')[0], safe=True)
            if duration:
                if ' ' in reason:
                    reason = reason.split(' ', 1)[-1]
                else:
                    reason = None
        elif duration:
            duration = parse_duration(duration)

        member = event.guild.get_member(user)
        if member:
            self.can_act_on(event, member.id)
            if not event.config.mute_role:
                raise CommandFail('mute is not setup on this server')

            if event.config.mute_role in member.roles:
                raise CommandFail(u'{} is already muted'.format(member.user))

            # If we have a duration set, this is a tempmute
            if duration:
                # Create the infraction
                Infraction.tempmute(self, event, member, reason, duration)
                self.queue_infractions()

                self.confirm_action(event, maybe_string(
                    reason,
                    u':ok_hand: {u} is now muted for {t} (`{o}`)',
                    u':ok_hand: {u} is now muted for {t}',
                    u=member.user,
                    t=humanize.naturaldelta(duration - datetime.utcnow()),
                ))
            else:
                existed = False
                # If the user is already muted check if we can take this from a temp
                #  to perma mute.
                if event.config.mute_role in member.roles:
                    existed = Infraction.clear_active(event, member.id, [Infraction.Types.TEMPMUTE])

                    # The user is 100% muted and not tempmuted at this point, so lets bail
                    if not existed:
                        raise CommandFail(u'{} is already muted'.format(member.user))

                Infraction.mute(self, event, member, reason)

                existed = u' [was temp-muted]' if existed else ''
                self.confirm_action(event, maybe_string(
                    reason,
                    u':ok_hand: {u} is now muted (`{o}`)' + existed,
                    u':ok_hand: {u} is now muted' + existed,
                    u=member.user,
                ))
        else:
            raise CommandFail('invalid user')

    @Plugin.command(
        'temprole',
        '<user:user|snowflake> <role:snowflake|str> <duration:str> [reason:str...]',
        level=CommandLevels.MOD)
    def temprole(self, event, user, role, duration, reason=None):
        member = event.guild.get_member(user)
        if not member:
            raise CommandFail('invalid user')

        self.can_act_on(event, member.id)
        role_id = role if isinstance(role, (int, long)) else event.config.role_aliases.get(role.lower())
        if not role_id or role_id not in event.guild.roles:
            raise CommandFail('invalid or unknown role')

        if role_id in member.roles:
            raise CommandFail(u'{} is already in that role'.format(member.user))

        expire_dt = parse_duration(duration)
        Infraction.temprole(self, event, member, role_id, reason, expire_dt)
        self.queue_infractions()

        self.confirm_action(event, maybe_string(
            reason,
            u':ok_hand: {u} is now in the {r} role for {t} (`{o}`)',
            u':ok_hand: {u} is now in the {r} role for {t}',
            r=event.guild.roles[role_id].name,
            u=member.user,
            t=humanize.naturaldelta(expire_dt - datetime.utcnow()),
        ))

    @Plugin.command('unmute', '<user:user|snowflake>', level=CommandLevels.MOD)
    def unmute(self, event, user, reason=None):
        # TOOD: eventually we should pull the role from the GuildMemberBackup if they arent in server
        member = event.guild.get_member(user)

        if member:
            self.can_act_on(event, member.id)
            if not event.config.mute_role:
                raise CommandFail('mute is not setup on this server')

            if event.config.mute_role not in member.roles:
                raise CommandFail(u'{} is not muted'.format(member.user))

            Infraction.clear_active(event, member.id, [Infraction.Types.MUTE, Infraction.Types.TEMPMUTE])

            self.call(
                'ModLogPlugin.create_debounce',
                event,
                ['GuildMemberUpdate'],
                role_id=event.config.mute_role,
            )

            member.remove_role(event.config.mute_role)

            self.call(
                'ModLogPlugin.log_action_ext',
                Actions.MEMBER_UNMUTED,
                event.guild.id,
                member=member,
                actor=unicode(event.author) if event.author.id != member.id else 'Automatic',
            )

            self.confirm_action(event, u':ok_hand: {} is now unmuted'.format(member.user))
        else:
            raise CommandFail('invalid user')

    @Plugin.command('kick', '<user:user|snowflake> [reason:str...]', level=CommandLevels.MOD)
    def kick(self, event, user, reason=None):
        member = event.guild.get_member(user)
        if member:
            self.can_act_on(event, member.id)
            Infraction.kick(self, event, member, reason)
            self.confirm_action(event, maybe_string(
                reason,
                u':ok_hand: kicked {u} (`{o}`)',
                u':ok_hand: kicked {u}',
                u=member.user,
            ))
        else:
            raise CommandFail('invalid user')

    @Plugin.command('mkick', parser=True, level=CommandLevels.MOD)
    @Plugin.parser.add_argument('users', type=long, nargs='+')
    @Plugin.parser.add_argument('-r', '--reason', default='', help='reason for modlog')
    def mkick(self, event, args):
        members = []
        for user_id in args.users:
            member = event.guild.get_member(user_id)
            if not member:
                # TODO: this sucks, batch these
                raise CommandFail('failed to kick {}, user not found'.format(user_id))

            if not self.can_act_on(event, member.id, throw=False):
                raise CommandFail('failed to kick {}, invalid permissions'.format(user_id))

            members.append(member)

        msg = event.msg.reply('Ok, kick {} users for `{}`?'.format(len(members), args.reason or 'no reason'))
        msg.chain(False).\
            add_reaction(GREEN_TICK_EMOJI).\
            add_reaction(RED_TICK_EMOJI)

        try:
            mra_event = self.wait_for_event(
                'MessageReactionAdd',
                message_id=msg.id,
                conditional=lambda e: (
                    e.emoji.id in (GREEN_TICK_EMOJI_ID, RED_TICK_EMOJI_ID) and
                    e.user_id == event.author.id
                )).get(timeout=10)
        except gevent.Timeout:
            return
        finally:
            msg.delete()

        if mra_event.emoji.id != GREEN_TICK_EMOJI_ID:
            return

        for member in members:
            Infraction.kick(self, event, member, args.reason)

        raise CommandSuccess('kicked {} users'.format(len(members)))

    @Plugin.command('ban', '<user:user|snowflake> [reason:str...]', level=CommandLevels.MOD)
    @Plugin.command('forceban', '<user:snowflake> [reason:str...]', level=CommandLevels.MOD)
    def ban(self, event, user, reason=None):
        member = None

        if isinstance(user, (int, long)):
            self.can_act_on(event, user)
            Infraction.ban(self, event, user, reason, guild=event.guild)
        else:
            member = event.guild.get_member(user)
            if member:
                self.can_act_on(event, member.id)
                Infraction.ban(self, event, member, reason, guild=event.guild)
            else:
                raise CommandFail('invalid user')

        self.confirm_action(event, maybe_string(
            reason,
            u':ok_hand: banned {u} (`{o}`)',
            u':ok_hand: banned {u}',
            u=member.user if member else user,
        ))

    @Plugin.command('softban', '<user:user|snowflake> [reason:str...]', level=CommandLevels.MOD)
    def softban(self, event, user, reason=None):
        """
        Ban then unban a user from the server (with an optional reason for the modlog)
        """
        member = event.guild.get_member(user)
        if member:
            self.can_act_on(event, member.id)
            Infraction.softban(self, event, member, reason)
            self.confirm_action(event, maybe_string(
                reason,
                u':ok_hand: soft-banned {u} (`{o}`)',
                u':ok_hand: soft-banned {u}',
                u=member.user,
            ))
        else:
            raise CommandFail('invald user')

    @Plugin.command('tempban', '<user:user|snowflake> <duration:str> [reason:str...]', level=CommandLevels.MOD)
    def tempban(self, event, duration, user, reason=None):
        member = event.guild.get_member(user)
        if member:
            self.can_act_on(event, member.id)
            expires_dt = parse_duration(duration)
            Infraction.tempban(self, event, member, reason, expires_dt)
            self.queue_infractions()
            self.confirm_action(event, maybe_string(
                reason,
                u':ok_hand: temp-banned {u} for {t} (`{o}`)',
                u':ok_hand: temp-banned {u} for {t}',
                u=member.user,
                t=humanize.naturaldelta(expires_dt - datetime.utcnow()),
            ))
        else:
            raise CommandFail('invalid user')

    @Plugin.command('warn', '<user:user|snowflake> [reason:str...]', level=CommandLevels.MOD)
    def warn(self, event, user, reason=None):
        member = None

        member = event.guild.get_member(user)
        if member:
            self.can_act_on(event, member.id)
            Infraction.warn(self, event, member, reason, guild=event.guild)
        else:
            raise CommandFail('invalid user')

        self.confirm_action(event, maybe_string(
            reason,
            u':ok_hand: warned {u} (`{o}`)',
            u':ok_hand: warned {u}',
            u=member.user if member else user,
        ))
