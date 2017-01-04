from rowboat.models.migrations import Migrate
from rowboat.models.guild import Guild


@Migrate.only_if(Migrate.missing, Guild, 'owner_id')
def add_guild_columns(m):
    m.add_columns(Guild,
        Guild.owner_id,
        Guild.name,
        Guild.icon,
        Guild.splash,
        Guild.region,
        Guild.last_ban_sync)
