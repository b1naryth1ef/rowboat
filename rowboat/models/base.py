from peewee import *
from playhouse.postgres_ext import *

REGISTERED_MODELS = []

# Create a database proxy we can setup post-init
database = Proxy()


class BaseModel(Model):
    class Meta:
        database = database

    @staticmethod
    def register(cls):
        REGISTERED_MODELS.append(cls)
        return cls


def init_db():
    database.initialize(PostgresqlExtDatabase('rowboat', user='rowboat'))

    for model in REGISTERED_MODELS:
        model.create_table(True)


def reset_db():
    init_db()

    for model in REGISTERED_MODELS:
        model.drop_table(True)
        model.create_table(True)
