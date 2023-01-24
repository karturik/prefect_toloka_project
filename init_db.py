import sqlite3
from contextlib import closing
from utils.sql_templates import DB, INIT_QUERY


def main():
    with closing(sqlite3.connect(DB)) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.executescript(INIT_QUERY)
            conn.commit()


if __name__ == '__main__':
    main()
