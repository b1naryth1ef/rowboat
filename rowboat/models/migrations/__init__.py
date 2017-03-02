from playhouse.migrate import PostgresqlMigrator, migrate
from rowboat.sql import database, init_db

COLUMN_EXISTS_SQL = '''
SELECT 1
FROM information_schema.columns
WHERE table_name=%s and column_name=%s;
'''

GET_NULLABLE_SQL = '''
SELECT is_nullable
FROM information_schema.columns
WHERE table_name=%s and column_name=%s;
'''


class Migrate(object):
    def __init__(self, rules, func):
        self.rules = rules
        self.func = func
        self.actions = []
        self.raw_actions = []
        self.m = PostgresqlMigrator(database)

    def run(self):
        conn = database.obj.get_conn()

        for rule in self.rules:
            with conn.cursor() as cur:
                if not rule(cur):
                    return

        self.func(self)
        self.apply()

    def apply(self):
        print 'Applying {} actions'.format(len(self.actions))
        migrate(*self.actions)

        print 'Executing {} raw queries'.format(len(self.raw_actions))
        conn = database.obj.get_conn()
        for query, args in self.raw_actions:
            print args
            with conn.cursor() as cur:
                cur.execute(query, args)

    def add_columns(self, table, *fields):
        for field in fields:
            self.actions.append(self.m.add_column(table._meta.db_table, field.name, field))

    def drop_not_nulls(self, table, *fields):
        for field in fields:
            self.actions.append(self.m.drop_not_null(table._meta.db_table, field.name))

    def add_not_nulls(self, table, *fields):
        for field in fields:
            self.actions.append(self.m.add_not_null(table._meta.db_table, field.name))

    def execute(self, query, params=None):
        self.raw_actions.append((query, params or []))

    @staticmethod
    def missing(table, field):
        def rule(cursor):
            cursor.execute(COLUMN_EXISTS_SQL, (table._meta.db_table, field))
            if len(cursor.fetchall()) == 0:
                return True
            return False
        return rule

    @staticmethod
    def nullable(table, field):
        def rule(cursor):
            cursor.execute(GET_NULLABLE_SQL, (table._meta.db_table, field))
            return cursor.fetchone()[0] == 'YES'
        return rule

    @staticmethod
    def non_nullable(table, field):
        def rule(cursor):
            cursor.execute(GET_NULLABLE_SQL, (table._meta.db_table, field))
            return cursor.fetchone()[0] == 'NO'
        return rule

    @classmethod
    def only_if(cls, check, table, *fields):
        def deco(func):
            rules = [check(table, i) for i in fields]
            cls(rules, func).run()
        return deco

    @classmethod
    def always(cls):
        def deco(func):
            cls([lambda c: True], func).run()
        return deco

init_db()
