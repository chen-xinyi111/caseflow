import hmac
import os
from contextlib import asynccontextmanager
from uuid import uuid4, UUID
import psycopg
from fastapi import FastAPI, HTTPException, Header
from redis.exceptions import RedisError
from lab.common import connect, initialize, queue
from opslens.contracts import JobInput, LabJob


@asynccontextmanager
async def lifespan(app):
    initialize()
    yield


app = FastAPI(title="OpsLens isolated text-statistics lab", lifespan=lifespan)


@app.exception_handler(psycopg.Error)
async def database_error(request, exc):
    from fastapi.responses import JSONResponse
    print("lab database unavailable: " + type(exc).__name__, flush=True)
    return JSONResponse(status_code=503, content={"detail": "实验数据库不可达，无法读取或保存任务"})


@app.get("/health")
def health():
    return {"status": "up", "instance": os.environ["LAB_INSTANCE"]}


@app.post("/jobs", response_model=LabJob)
def submit(data: JobInput):
    with connect() as conn:
        row = conn.execute(
            "INSERT INTO jobs(id,request_id,text) VALUES (%s,%s,%s) ON CONFLICT(request_id) DO NOTHING RETURNING *",
            (uuid4(), data.request_id, data.text),
        ).fetchone()
        if not row:
            row = conn.execute("SELECT * FROM jobs WHERE request_id=%s", (data.request_id,)).fetchone()
            if row["text"] != data.text:
                raise HTTPException(409, "相同请求编号已用于不同文本")
    if row["status"] == "queued":
        try:
            queue().rpush("lab:jobs", str(row["id"]))
            error = None
        except RedisError as exc:
            error = "redis_unavailable"
            print("lab queue unavailable: " + type(exc).__name__, flush=True)
        with connect() as conn:
            row = conn.execute("UPDATE jobs SET queue_error=%s,updated_at=now() WHERE id=%s RETURNING *", (error, row["id"])).fetchone()
    return row


@app.get("/jobs/by-request/{request_id}", response_model=LabJob)
def job_by_request(request_id: UUID, x_opslens_key: str = Header(default="")):
    if not hmac.compare_digest(os.environ['LAB_DIAGNOSTIC_KEY'],x_opslens_key):
        raise HTTPException(403,'诊断访问未授权')
    with connect() as conn:
        row=conn.execute('SELECT * FROM jobs WHERE request_id=%s',(request_id,)).fetchone()
    if not row:raise HTTPException(404,'此请求尚无可读取任务；不重新提交')
    return row


@app.get("/jobs/{job_id}", response_model=LabJob)
def job(job_id: UUID):
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=%s", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "实验任务不存在")
    return row


@app.get("/diagnostics")
def diagnostics(x_opslens_key: str = Header(default="")):
    expected = os.environ["LAB_DIAGNOSTIC_KEY"]
    if not hmac.compare_digest(expected, x_opslens_key):
        raise HTTPException(403, "诊断访问未授权")
    data = {"instance": os.environ["LAB_INSTANCE"], "database": {"reachable": False}, "redis": {"reachable": False}}
    try:
        with connect() as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            data["database"] = {
                "reachable": True,
                "jobs": conn.execute("SELECT id,status,result,worker_version,queue_error,created_at,updated_at FROM jobs ORDER BY created_at DESC LIMIT 12").fetchall(),
                "activity": conn.execute("""
                    SELECT pid,application_name,state,wait_event_type,wait_event,
                           pg_blocking_pids(pid) AS blocking_pids
                    FROM pg_stat_activity
                    WHERE datname=current_database() AND application_name LIKE 'opslens-%'
                """).fetchall(),
            }
    except psycopg.Error as exc:
        data["database"]["error"] = type(exc).__name__
    try:
        client = queue()
        client.ping()
        data["redis"] = {"reachable": True, "queued": client.llen("lab:jobs")}
    except RedisError as exc:
        data["redis"]["error"] = type(exc).__name__
    return data
