from rowboat.models.migrations import Migrate
from rowboat.models.channel import Guild


@Migrate.only_if(Migrate.missing, Guild, 'next_audit_log_sync')
def add_guild_next_audit_log_sync(m):
    m.add_columns(Guild, Guild.next_audit_log_sync)
