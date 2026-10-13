import os
import psycopg
from psycopg.rows import dict_row
from redis import Redis
from redis.retry import Retry
from redis.backoff import NoBackoff


def connect():
    return psycopg.connect(os.environ["LAB_DATABASE_URL"], row_factory=dict_row, connect_timeout=3)


def queue():
    return Redis.from_url(os.environ["LAB_REDIS_URL"], socket_connect_timeout=2, socket_timeout=3, decode_responses=True, retry=Retry(NoBackoff(), 0))


def initialize():
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id uuid PRIMARY KEY,
                request_id uuid NOT NULL UNIQUE,
                text text NOT NULL,
                status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed')),
                result jsonb,
                worker_version text,
                queue_error text,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """)
