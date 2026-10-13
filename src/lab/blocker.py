import os
import time
from lab.common import connect

if __name__ == "__main__":
    with connect() as conn:
        conn.execute("SET application_name = 'opslens-injected-blocker'")
        row = conn.execute("SELECT id FROM jobs WHERE id=%s FOR UPDATE", (os.environ["BLOCK_JOB_ID"],)).fetchone()
        if not row:
            raise RuntimeError("Only a prepared experiment job may be locked")
        print("LOCK_READY job=" + str(row["id"]), flush=True)
        time.sleep(600)
