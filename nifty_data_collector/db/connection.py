"""
db/connection.py — PostgreSQL pool using Railway DATABASE_URL
"""

import psycopg2
from psycopg2 import pool
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import DATABASE_URL

_pool = None

def get_pool():
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2, maxconn=10, dsn=DATABASE_URL
        )
    return _pool

def get_conn():
    return get_pool().getconn()

def release_conn(conn):
    get_pool().putconn(conn)

class DBConn:
    def __enter__(self):
        self.conn = get_conn()
        self.conn.autocommit = False
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.conn.rollback()
        else:
            self.conn.commit()
        release_conn(self.conn)
