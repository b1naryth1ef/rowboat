from holster.enum import Enum

from rowboat.models.migrations import Migrate

BeforeTypes = Enum(
    'KICK',
    'TEMPBAN',
    'SOFTBAN',
    'BAN',
)

AfterTypes = Enum(
    'MUTE',
    'KICK',
    'TEMPBAN',
    'SOFTBAN',
    'BAN',
    bitmask=False,
)


@Migrate.always()
def alter_guild_columns(m):
    for typ in BeforeTypes.attrs:
        m.execute('UPDATE infractions SET type=%s WHERE type=%s', (
            AfterTypes[typ.name].index, typ.index
        ))
