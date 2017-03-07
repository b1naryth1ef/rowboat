from rowboat.models.migrations import Migrate
from rowboat.models.guild import Guild


@Migrate.only_if(Migrate.missing, Guild, 'config_raw')
def add_guild_columns(m):
    m.add_columns(Guild, Guild.config_raw)
