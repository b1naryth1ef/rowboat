import time

from rowboat.models.migrations import Migrate
from rowboat.models.message import Message

from rowboat.sql import database as db


def backfill_column(table, old_columns, new_columns):
    total = table.select().count()

    q = table.select(
        table._meta.primary_key,
        *old_columns
    ).tuples()

    idx = 0
    modified = 0

    start = time.time()
    with db.transaction() as txn:
        for values in q:
            idx += 1

            if idx % 10000 == 0:
                print '[%ss] Backfilling %s %s/%s (wrote %s)' % (time.time() - start, str(table), idx, total, modified)

            if modified % 1000:
                txn.commit()

            obj = {new_column.name: values[i + 1] for i, new_column in enumerate(new_columns)}
            if not any(obj.values()):
                continue

            modified += 1
            table.update(
                **{new_column.name: values[i + 1] for i, new_column in enumerate(new_columns)}
            ).where(table._meta.primary_key == values[0]).execute()

    txn.commit()
    print 'DONE, %s scanned %s written' % (idx, modified)


@Migrate.only_if(Migrate.missing, Message, 'mentions_new')
def add_guild_columns(m):
    m.add_columns(
        Message,
        Message.mentions_new,
        Message.emojis_new,
        Message.attachments_new,
        Message.embeds,
    )


@Migrate.always()
def backfill_data(m):
    backfill_column(
        Message,
        [Message.mentions, Message.emojis, Message.attachments],
        [Message.mentions_new, Message.emojis_new, Message.attachments_new])
