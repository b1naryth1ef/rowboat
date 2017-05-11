from rowboat.models.migrations import Migrate
from rowboat.models.channel import Channel


@Migrate.only_if(Migrate.missing, Channel, 'type_')
def add_channel_type_column(m):
    m.add_columns(Channel, Channel.type_)
