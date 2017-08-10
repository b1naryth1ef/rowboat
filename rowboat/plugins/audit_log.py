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

    @Plugin.schedule(60, init=False)
    def poll_audit_log(self):
        to_poll = filter(bool, (
            self.client.state.guilds.get(guild.guild_id)
            for guild in Guild.select().where(
                (Guild.enabled == 1) &
                (
                    (Guild.next_audit_log_sync > datetime.utcnow()) |
                    (Guild.next_audit_log_sync >> None)
                )
            )
        ))

        for guild in to_poll:
            pages = self.poll_guild(guild)
            self.log.info('Polled audit logs for guild %s (%s), %s pages', guild.id, guild.name, pages)

            if not pages or pages > 1:
                next_sync = datetime.utcnow() + timedelta(seconds=120)
            else:
                next_sync = datetime.utcnow() + timedelta(seconds=360)

            Guild.update(
                next_audit_log_sync=next_sync
            ).where(
                (Guild.guild_id == guild.id)
            ).execute()

    def poll_guild(self, guild):
        last_entry_id = rdb.get(LAST_ENTRY_KEY.format(guild.id))

        # If we haven't polled this guild before (or it has no entries), attempt
        #  to cache the last entry id so we can start emitting audit log entries
        if not last_entry_id:
            entry = next(guild.get_audit_log_entries(limit=1), None)
            if entry:
                rdb.set(LAST_ENTRY_KEY.format(guild.id), entry.id)
            return 0

        entry = None
        first_entry = None

        # Bounded while loop
        for page in range(100):
            entries = guild.get_audit_log_entries(before=(entry.id if entry else None))

            for entry in entries:
                if not first_entry:
                    first_entry = entry

                if entry.id == last_entry_id:
                    break

                self.emitter.emit(entry.action_type, entry)

        rdb.set(LAST_ENTRY_KEY.format(guild.id), first_entry.id)
        return page + 1
