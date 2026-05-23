import os
import time
import psycopg2

from psycopg2.extras import RealDictCursor
from psycopg2 import OperationalError

DB_URL = os.getenv("DB_URL")


def get_db():
    max_retries = 3

    for i in range(max_retries):
        try:
            return psycopg2.connect(
                DB_URL,
                cursor_factory=RealDictCursor,
                sslmode="require"
            )

        except OperationalError:
            if i == max_retries - 1:
                raise

            time.sleep(2 ** i)