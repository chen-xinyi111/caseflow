from pathlib import Path
from uuid import uuid4
import json,pytest,psycopg
from psycopg.rows import dict_row
from fastapi.testclient import TestClient
from opslens import db
from opslens.config import platform_dsn
from opslens.api import app
from opslens.rag import store as kb
from opslens.investigation import store,worker
from opslens.rag.provider import ModelBlocked
from test_investigation_core import FakeTools,FakeModel
pytestmark=pytest.mark.integration
@pytest.fixture
def client(monkeypatch):
    name="inv_test_"+uuid4().hex
    with db.connect() as c:c.execute(psycopg.sql.SQL("CREATE SCHEMA {}").format(psycopg.sql.Identifier(name)))
    def connect():return psycopg.connect(platform_dsn(),row_factory=dict_row,options=f"-c search_path={name},public")
    monkeypatch.setattr(db,"connect",connect)
    try:
        with connect() as c:
            for path in ["schema.sql","durability.sql","rag/schema.sql","investigation/schema.sql","recovery/schema.sql"]:
                c.execute((Path("src/opslens")/path).read_text(encoding="utf-8"))
        kb.ingest("test.txt",b"worker test","fixture","fixture-test","1",["v1"])
        index=kb.create_index("semantic")
        with connect() as c:c.execute("UPDATE kb_indexes SET status='ready',active=true,finished_at=now() WHERE id=%s",(index["input"]["index_id"],))
        yield TestClient(app)
    finally:
        with connect() as c:c.execute(psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(psycopg.sql.Identifier(name)))
def submit(client):
    body={"request_id":str(uuid4()),"question":"worker waiting","target":"fault"}
    response=client.post("/api/v1/investigations",json=body)
    assert response.status_code==202,response.text
    return body,response.json()
def test_idempotent_inputs_persistent_report_and_progress(client):
    body,task=submit(client)
    assert client.post("/api/v1/investigations",json=body).json()["id"]==task["id"]
    assert client.post("/api/v1/investigations",json=body|{"question":"changed"}).status_code==409
    assert client.post("/api/v1/investigations",json=body|{"target":"project-one"}).status_code==422
    worker.process(worker.claim(),FakeModel(),FakeTools())
    result=client.get("/api/v1/investigations/"+task["id"]).json()
    assert result["report"]["assessment"]=="supported" and result["metrics"]["model_calls"]==2
    events=client.get("/api/v1/investigations/"+task["id"]+"/events").text
    ids=[int(x[4:]) for x in events.splitlines() if x.startswith("id: ")]
    replay=client.get("/api/v1/investigations/"+task["id"]+"/events",headers={"Last-Event-ID":str(ids[0])}).text
    assert f"id: {ids[0]}\n" not in replay and f"id: {ids[-1]}\n" in replay
    assert client.get("/api/v1/investigations/"+task["id"]+"/events?after=-1").status_code==400
    with db.connect() as c:assert c.execute("SELECT count(*) AS n FROM investigation_steps").fetchone()["n"]>=5
def test_model_quota_block_is_visible_without_report(client):
    _,task=submit(client)
    class Blocked:
        def generate(self,*args):raise ModelBlocked("quota fixture")
    worker.process(worker.claim(),Blocked(),FakeTools())
    result=client.get("/api/v1/investigations/"+task["id"]).json()
    assert result["status"]=="blocked" and result["report"] is None and result["error"]=="quota fixture"
def test_fake_citation_fails_without_success_report(client):
    _,task=submit(client)
    from test_investigation_core import report
    class Invalid:
        def generate(self,*args):return {"role":"assistant","content":json.dumps(report(ref="nonexistent"))}
    worker.process(worker.claim(),Invalid(),FakeTools())
    result=client.get("/api/v1/investigations/"+task["id"]).json()
    assert result["status"]=="failed" and result["report"] is None


def test_model_audit_handles_null_usage_and_preserves_http_failure(client):
    from opslens.rag.provider import DatabaseLedger
    call=uuid4()
    with db.connect() as c:c.execute("INSERT INTO kb_model_calls(id,model,status,estimated_tokens,input_payload) VALUES (%s,'qwen3.8-max','reserved',100,'{}')",(call,))
    DatabaseLedger().finish(call,{"usage":None,"error":{"message":"fixture error"}},"model_http_400")
    with db.connect() as c:row=c.execute("SELECT * FROM kb_model_calls WHERE id=%s",(call,)).fetchone()
    assert row["status"]=="failed" and row["actual_tokens"] is None and row["response_payload"]["error"]["message"]=="fixture error"
