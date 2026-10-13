import os
import time
import psycopg
from psycopg.types.json import Jsonb
from redis.exceptions import RedisError
from lab.common import connect, queue
from lab.processing import count_text


def run():
    version = os.environ.get("LAB_WORKER_VERSION", "v1")
    print("worker started version=" + version, flush=True)
    while True:
        try:
            item = queue().blpop("lab:jobs", timeout=1)
            if not item:
                continue
            job_id = item[1]
            try:
                with connect() as conn:
                    conn.execute("SET application_name = 'opslens-worker'")
                    row = conn.execute("UPDATE jobs SET status='running',updated_at=now() WHERE id=%s AND status='queued' RETURNING *", (job_id,)).fetchone()
                    if row:
                        result = count_text(row["text"])
                        if version == "v0-bug":
                            result["word_count"] = 1
                        conn.execute("UPDATE jobs SET status='completed',result=%s,worker_version=%s,updated_at=now() WHERE id=%s", (Jsonb(result), version, job_id))
                        print(f"job completed id={job_id} version={version} result={result}", flush=True)
            except psycopg.Error as exc:
                print("worker database failure: " + type(exc).__name__, flush=True)
                queue().rpush("lab:jobs", job_id)
                time.sleep(1)
        except (RedisError, psycopg.Error) as exc:
            print("worker dependency failure: " + str(exc), flush=True)
            time.sleep(1)


if __name__ == "__main__":
    run()
