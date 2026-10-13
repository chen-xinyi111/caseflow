from pathlib import Path
from contextlib import contextmanager
from datetime import datetime,timezone
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from opslens.config import platform_dsn

CASES = [
    ("database_stopped", "数据库停止", "实验数据库停止时，任务提交或读取失败。"),
    ("redis_stopped", "Redis 停止", "实验队列不可达时，任务保留入队错误且无法正常处理。"),
    ("worker_stopped", "worker 停止", "队列和数据库仍可用，但后台处理进程已停止。"),
    ("wrong_address", "依赖地址错误", "worker 的依赖主机配置错误，无法连接队列。"),
    ("transaction_blocked", "事务阻塞", "专门注入的事务持有任务行锁，后台任务等待锁释放。"),
    ("wrong_version", "应用版本错误", "实验错误版本完成任务，但实际统计结果与业务规则不符。"),
]


@contextmanager
def connect():
    with psycopg.connect(platform_dsn(), row_factory=dict_row, connect_timeout=3) as conn:
        from opslens.durability import ACTIVE, INTERNAL
        rt=ACTIVE.get()
        if rt is not None and not INTERNAL.get():rt.guard(conn,lock=False)
        yield conn


def initialize():
    with connect() as conn:
        conn.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
        conn.execute(Path(__file__).with_name("durability.sql").read_text(encoding="utf-8"))
        conn.execute(Path(__file__).with_name("rag").joinpath("schema.sql").read_text(encoding="utf-8"))
        conn.execute(Path(__file__).with_name("investigation").joinpath("schema.sql").read_text(encoding="utf-8"))
        conn.execute(Path(__file__).with_name('recovery').joinpath('schema.sql').read_text(encoding='utf-8'))
        conn.execute(Path(__file__).with_name('evaluation').joinpath('schema.sql').read_text(encoding='utf-8'))
        conn.execute(Path(__file__).with_name('evaluation').joinpath('review.sql').read_text(encoding='utf-8'))
        recovery_stopped=conn.execute("UPDATE recovery_plans SET status='manual',error='平台重启，动作或验证结果待确认；只读复核，不重复执行',finished_at=now() WHERE status IN ('executing','verifying') AND NOT EXISTS (SELECT 1 FROM durable_tasks d WHERE d.family='recovery' AND d.task_id=recovery_plans.id) RETURNING id").fetchall()
        for item in recovery_stopped:
            conn.execute("INSERT INTO recovery_events(plan_id,kind,payload) VALUES (%s,'finished',%s)",(item['id'],Jsonb({'status':'manual','message':'平台重启，已有动作记录保留，禁止自动重放'})))
        stopped=conn.execute("UPDATE investigations SET status='failed',error='平台重启，调查中断；已有记录保留，请重新提交',finished_at=now() WHERE status='running' AND NOT EXISTS (SELECT 1 FROM durable_tasks d WHERE d.family='investigation' AND d.task_id=investigations.id) RETURNING id,collaboration,verification").fetchall()
        for item in stopped:
            collaboration=item["collaboration"]
            if collaboration:
                for task in collaboration.get("tasks",[]):
                    if task["status"] in ("pending","scheduled","running"):
                        task.update(status="failed" if task["status"]=="running" else "skipped",error="平台重启，子任务中断")
                collaboration["merge_status"]="failed"
                conn.execute("UPDATE investigations SET collaboration=%s WHERE id=%s",(Jsonb(collaboration),item["id"]))
            audit=item['verification']
            if audit:
                audit.update(status='interrupted',stop_reason='平台重启，核验中断；已有记录保留')
                for row in audit.get('rounds',[]):
                    if not row.get('finished_at'):row.update(error='平台重启，核验中断',finished_at=datetime.now(timezone.utc).isoformat())
                for row in audit.get('replans',[]):
                    if row.get('status') in ('planned','running'):row['status']='interrupted'
                conn.execute('UPDATE investigations SET verification=%s WHERE id=%s',(Jsonb(audit),item['id']))
            conn.execute("INSERT INTO investigation_events(investigation_id,kind,payload) VALUES (%s,'finished',%s)",(item["id"],Jsonb({"status":"failed","message":"平台重启，调查已中断"})))
        interrupted=conn.execute("UPDATE kb_tasks SET status='failed',error='平台重启，任务中断；请重新提交',finished_at=now() WHERE status='running' AND NOT EXISTS (SELECT 1 FROM durable_tasks d WHERE d.family='knowledge' AND d.task_id=kb_tasks.id) RETURNING id,input,kind").fetchall()
        for item in interrupted:
            conn.execute("INSERT INTO kb_events(task_id,kind,payload) VALUES (%s,'finished',%s)",(item["id"],Jsonb({"status":"failed","message":"平台重启，原有索引和文档保留"})))
            if item["kind"]=="index":
                conn.execute("UPDATE kb_indexes SET status='failed',error='构建中断',finished_at=now() WHERE id=%s AND status='building'",(item["input"]["index_id"],))
        for case in CASES:
            conn.execute("INSERT INTO fault_cases(id,title,description) VALUES (%s,%s,%s) ON CONFLICT(id) DO NOTHING", case)
        # Read-only collection can safely be re-collected after process interruption.
        conn.execute("UPDATE collections SET status='queued' WHERE status='running' AND NOT EXISTS (SELECT 1 FROM durable_tasks d WHERE d.family='collection' AND d.task_id=collections.id)")


def append_event(conn, collection_id, kind, payload):
    conn.execute("INSERT INTO collection_events(collection_id,kind,payload) VALUES (%s,%s,%s)", (collection_id, kind, Jsonb(payload)))
