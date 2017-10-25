from rowboat.models.migrations import Migrate
from rowboat.models.user import User


@Migrate.only_if(Migrate.missing, User, 'settings')
def add_user_settings_column(m):
    m.add_columns(User, User.settings)
