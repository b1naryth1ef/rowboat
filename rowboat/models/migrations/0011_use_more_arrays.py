from rowboat.models.migrations import Migrate
from rowboat.models.guild import GuildMemberBackup, GuildEmoji


@Migrate.only_if(Migrate.missing, GuildEmoji, 'roles_new')
def add_guild_emoji_columns(m):
    m.add_columns(
        GuildEmoji,
        GuildEmoji.roles_new,
    )


@Migrate.only_if(Migrate.missing, GuildMemberBackup, 'roles_new')
def add_guild_member_backup_columns(m):
    m.add_columns(
        GuildMemberBackup,
        GuildMemberBackup.roles_new
    )


@Migrate.always()
def backfill(m):
    m.backfill_column(
        GuildEmoji,
        [GuildEmoji.roles],
        [GuildEmoji.roles_new])

    m.backfill_column(
        GuildMemberBackup,
        [GuildMemberBackup.roles],
        [GuildMemberBackup.roles_new],
        pkeys=[GuildMemberBackup.user_id, GuildMemberBackup.guild_id])
