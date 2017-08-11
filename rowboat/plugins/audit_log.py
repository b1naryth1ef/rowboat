from datetime import datetime, timedelta

import gevent
from holster.emitter import Emitter

from rowboat.redis import rdb
from rowboat.plugins import BasePlugin as Plugin
from rowboat.models.guild import Guild

LAST_ENTRY_KEY = 'guild:laleik:{}'


class AuditLogPlugin(Plugin):
    global_plugin = True

    def load(self, ctx):
        super(AuditLogPlugin, self).load(ctx)

        self.emitter = Emitter(gevent.spawn)

    @Plugin.command('poll', '<guild_id:snowflake>', level=-1, group='auditlogs')
    def command_poll(self, event, guild_id):
        Guild.update(
            next_audit_log_sync=datetime.utcnow()
        ).where(
            (Guild.guild_id == guild_id)
        ).execute()
        event.msg.reply('Ok, queued that guild for audit log sync')

    @Plugin.schedule(10, init=True)
    def poll_audit_log(self):
        to_poll = filter(bool, (
            self.client.state.guilds.get(guild.guild_id)
            for guild in Guild.select().where(
                (Guild.enabled == 1) &
                (
                    (Guild.next_audit_log_sync < datetime.utcnow()) |
                    (Guild.next_audit_log_sync >> None)
                )
            )
        ))

        for guild in to_poll:
            entries = self.poll_guild(guild)
            self.log.info('Polled audit logs for guild %s (%s), %s entries', guild.id, guild.name, entries)

            if not entries or entries > 100:
                next_sync = datetime.utcnow() + timedelta(seconds=120)
            else:
                next_sync = datetime.utcnow() + timedelta(seconds=360)

            Guild.update(
                next_audit_log_sync=next_sync
            ).where(
                (Guild.guild_id == guild.id)
            ).execute()

    def poll_guild(self, guild):
        last_entry_id = int(rdb.get(LAST_ENTRY_KEY.format(guild.id)) or 0)

        # If we haven't polled this guild before (or it has no entries), attempt
        #  to cache the last entry id so we can start emitting audit log entries
        if not last_entry_id:
            entries = guild.get_audit_log_entries(limit=1)
            if entries:
                rdb.set(LAST_ENTRY_KEY.format(guild.id), entries[0].id)
            return 0

        first_entry = None
        entries = 0

        # Iterate over the paginator
        for entry in guild.audit_log_iter():
            # Make sure to set the first entry
            if not first_entry:
                first_entry = entry

            # Break if we've hit the last entry
            if entry.id == last_entry_id:
                break

            entries += 1
            self.emitter.emit(entry.action_type, entry)

        rdb.set(LAST_ENTRY_KEY.format(guild.id), first_entry.id)
        return entries
