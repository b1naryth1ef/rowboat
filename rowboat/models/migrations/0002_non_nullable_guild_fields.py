from rowboat.models.migrations import Migrate
from rowboat.models.guild import Guild


@Migrate.only_if(Migrate.nullable, Guild, 'owner_id')
def alter_guild_columns(m):
    m.add_not_nulls(Guild,
        Guild.owner_id,
        Guild.name,
        Guild.region)
