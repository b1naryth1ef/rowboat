from rowboat.models.migrations import Migrate
from rowboat.models.message import Message

from rowboat.sql import database as db
from playhouse.postgres_ext import ServerSide


def backfill_column(table, old_column, new_column, apply_func=None):
    total = table.select().count()

    q = ServerSide(table.select(
        table._meta.primary_key,
        old_column
    )).tuples()

    idx = 0

    with db.transaction() as txn:
        for oid, old_value in q:
            idx += 1
            new_value = apply_func(old_value) if apply_func else old_value

            if idx % 1000 == 0:
                print 'Backfilling %s %s/%s' % (str(table), idx, total)
                txn.commit()

            table.update(
                **{new_column: new_value}
            ).where(table._meta.primary_key == oid).execute()

    txn.commit()


@Migrate.only_if(Migrate.missing, Message, 'mentions_new')
def add_guild_columns(m):
    m.add_columns(
        Message,
        Message.mentions_new,
        Message.emojis_new,
        Message.attachments_new,
        Message.embeds,
    )


@Migrate.always
def backfill_data():
    backfill_column(Message, Message.mentions, Message.mentions_new)
    backfill_column(Message, Message.emojis, Message.emojis_new)
    backfill_column(Message, Message.attachments, Message.attachments_new)
