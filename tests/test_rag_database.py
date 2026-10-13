"""Real pgvector/SQL mechanics with explicit fixture models in a disposable schema."""
import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4
import pytest
import psycopg
from psycopg.rows import dict_row
from fastapi.testclient import TestClient
from opslens import db
from opslens.config import platform_dsn
from opslens.rag import store,service,worker
from opslens.rag.contracts import SearchInput
from opslens.rag.provider import ModelBlocked
from opslens.api import app

pytestmark=pytest.mark.integration

@pytest.fixture
def isolated(monkeypatch):
    name="kb_test_"+uuid4().hex
    assert re.fullmatch(r"kb_test_[a-f0-9]{32}",name)
    with db.connect() as conn:conn.execute(psycopg.sql.SQL("CREATE SCHEMA {}").format(psycopg.sql.Identifier(name)))
    def connect():return psycopg.connect(platform_dsn(),row_factory=dict_row,options=f"-c search_path={name},public")
    monkeypatch.setattr(db,"connect",connect)
    try:
        with connect() as conn:
            conn.execute(Path("src/opslens/durability.sql").read_text(encoding="utf-8"))
            conn.execute(Path("src/opslens/rag/schema.sql").read_text(encoding="utf-8"))
        yield TestClient(app)
    finally:
        with connect() as conn:conn.execute(psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(psycopg.sql.Identifier(name)))

def doc(source="fixture-worker",version="1",text="worker 停止，任务等待处理。",applies=None):
    return store.ingest("guide.md",text.encode(),"worker 运行手册",source,version,applies or ["v1"])

def keyword_index(ids=None):
    value=store.create_index("keyword",ids)
    worker.process(worker.claim())
    with db.connect() as conn:return store.task(conn,value["id"])["result"]["index_id"]

def test_version_dedup_collision_and_source_citation(isolated):
    first=doc();assert doc()["id"]==first["id"]
    with pytest.raises(FileExistsError):doc(text="changed")
    chunk=first["chunks"][0]
    with db.connect() as conn:
        cited=store.citation(conn,chunk["id"])
        assert cited["page_text"][chunk["start_char"]:chunk["end_char"]]==chunk["content"]
    second=doc(version="2",text="新版 worker 运行手册：需要核对队列。")
    assert second["id"]!=first["id"]
    with db.connect() as conn:
        assert store.citation(conn,chunk["id"])["document"]["doc_version"]=="1"
    index=keyword_index()
    with db.connect() as conn:
        ids=conn.execute("SELECT document_ids FROM kb_indexes WHERE id=%s",(index,)).fetchone()["document_ids"]
    assert str(second["id"]) in ids and str(first["id"]) not in ids

def test_snapshot_filters_app_version_and_retains_old_index(isolated):
    first=doc();wrong=doc("wrong-version","1","worker 停止 任务等待处理。",["v2"])
    index=keyword_index()
    result=service.search({"query":"worker 停止","mode":"keyword","app_version":"v1","index_id":index})
    assert result["hits"] and all(hit["document"]["id"]==str(first["id"]) for hit in result["hits"])
    newer=doc(version="2",text="新版其他资料")
    new_index=keyword_index()
    old=service.search({"query":"worker 停止","mode":"keyword","app_version":"v1","index_id":index})
    assert old["hits"][0]["document"]["doc_version"]=="1" and new_index!=index
    assert service.search({"query":"zzzznomatch","mode":"keyword","app_version":"v1","index_id":index})["hits"]==[]

def test_http_import_original_progress_replay_and_html_is_data(isolated):
    text="<script>alert('not executable')</script>\nworker 停止后的资料。"
    response=isolated.post("/api/v1/kb/documents",data={"title":"测试资料","source":"fixture-http","doc_version":"1","applicable_versions":"v1"},files={"file":("guide.txt",text.encode(),"text/plain")})
    assert response.status_code==201,response.text
    docid=response.json()["id"]
    assert isolated.get(f"/api/v1/kb/documents/{docid}/original").content==text.encode()
    assert isolated.get(f"/api/v1/kb/documents/{docid}").json()["pages"][0]==text
    task=isolated.post("/api/v1/kb/indexes",json={"mode":"keyword","document_ids":[docid]})
    assert task.status_code==202
    worker.process(worker.claim())
    cid=task.json()["id"]
    events=isolated.get(f"/api/v1/kb/tasks/{cid}/events").text
    ids=[int(line[4:]) for line in events.splitlines() if line.startswith("id: ")]
    assert len(ids)>=3 and 'checkpoint' in events
    replay=isolated.get(f"/api/v1/kb/tasks/{cid}/events",headers={"Last-Event-ID":str(ids[0])}).text
    assert f"id: {ids[0]}\n" not in replay and f"id: {ids[-1]}\n" in replay
    search=isolated.post("/api/v1/kb/searches",json={"query":"worker 停止","mode":"keyword","index_id":isolated.get(f"/api/v1/kb/tasks/{cid}").json()["result"]["index_id"]})
    assert search.status_code==202
    worker.process(worker.claim())
    assert isolated.get("/api/v1/kb/tasks/"+search.json()["id"]).json()["result"]["hits"]
    assert isolated.post("/api/v1/kb/documents",data={"title":"x","source":"x","doc_version":"1"},files={"file":("bad.pdf",b"bad","application/pdf")}).status_code==400
    assert isolated.get(f"/api/v1/kb/tasks/{cid}/events?after=-1").status_code==400
    assert isolated.get(f"/api/v1/kb/citations/{uuid4()}").status_code==404

class FixtureProvider:
    """A test double only: no production configuration exposes this provider."""
    def __init__(self):self.calls=0
    def embed(self,texts,text_type="document"):
        self.calls+=1
        return [[1.,0.]+[0.]*1022 if "worker" in t else [0.,1.]+[0.]*1022 for t in texts]
    def rerank(self,query,texts):
        return [{"index":i,"relevance_score":.9 if "worker" in text else .1} for i,text in enumerate(texts)]

def test_real_pgvector_and_rrf_rerank_mechanics_with_fixture_only(isolated):
    first=doc();noise=store.ingest("noise.txt",b"database policy","noise","fixture-noise","1",["v1"])
    task=store.create_index("semantic")
    provider=FixtureProvider()
    index=task["input"]["index_id"]
    service.build_index(index,provider)
    payload={"query":"worker 停止","mode":"rerank","app_version":"v1","index_id":index}
    result=service.search(payload,provider)
    assert result["hits"][0]["document"]["id"]==str(first["id"])
    assert result["hits"][0]["routes"]==["bm25","vector"]
    assert result["hits"][0]["rerank_score"]==.9
    assert result["counts"]["bm25"]<=20 and result["counts"]["vector"]<=20 and result["counts"]["reranked"]<=20 and len(result["hits"])<=6
    before=provider.calls;service.search(payload,provider)
    assert provider.calls==before

def test_blocked_model_keeps_previous_valid_index_and_no_fake_vectors(isolated,monkeypatch):
    doc()
    index=keyword_index()
    with db.connect() as conn:
        active=conn.execute("SELECT id FROM kb_indexes WHERE active").fetchone()["id"]
    semantic=store.create_index("semantic")
    monkeypatch.delenv("DASHSCOPE_APIKEY",raising=False)
    worker.process(worker.claim())
    with db.connect() as conn:
        row=store.task(conn,semantic["id"])
        assert row["status"]=="blocked"
        assert conn.execute("SELECT count(*) AS n FROM kb_embeddings").fetchone()["n"]==0
        assert conn.execute("SELECT count(*) AS n FROM kb_model_calls").fetchone()["n"]==0
        assert conn.execute("SELECT id FROM kb_indexes WHERE active").fetchone()["id"]==active
