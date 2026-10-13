import subprocess
from unittest.mock import Mock
from fastapi.testclient import TestClient
from opslens.api import app
from opslens import collector
from opslens.contracts import LabName


def test_evidence_logs_include_stderr(monkeypatch):
    monkeypatch.setattr(collector, "docker_executable", lambda: "docker")
    monkeypatch.setattr(collector.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "stdout-log\n", "stderr-failure\n"))
    assert "stderr-failure" in collector.docker(["logs", "target"])


def test_container_identity_is_checked_before_reading_logs(monkeypatch):
    import json
    foreign = {"Config":{"Labels":{"com.docker.compose.project":"agent-commerce-stage3","com.docker.compose.service":"worker","io.opslens.scope":"lab"}}}
    def fake_docker(args, **kwargs):
        return "foreign-id" if args[0] == "ps" else json.dumps([foreign])
    monkeypatch.setattr(collector, "docker", fake_docker)
    monkeypatch.setattr(collector, "diagnostics", lambda lab: {"instance":"fault"})
    items = collector.collect(LabName.fault)
    assert any(i["source"] == "docker.services" and not i["success"] for i in items)
    assert not any(i["source"].startswith("docker.logs.") for i in items)


def test_host_origin_and_validation_boundaries():
    client = TestClient(app)
    assert client.get("/api/v1/fault-cases", headers={"origin":"https://evil.example"}).status_code == 403
    assert client.get("/api/v1/fault-cases", headers={"host":"evil.example"}).status_code == 403
    assert client.post("/api/v1/lab-jobs",json={"text":"hello","request_id":"not-a-uuid","lab":"healthy"}).status_code == 422
    assert client.post("/api/v1/collections",json={"lab":"qinghe","command":"stop"}).status_code == 422
    assert client.post("/api/v1/execute",json={"command":"stop"}).status_code == 404


def test_invalid_sse_cursors_rejected_before_storage_access():
    from uuid import uuid4
    client = TestClient(app)
    url = f"/api/v1/collections/{uuid4()}/events"
    assert client.get(url,headers={"Last-Event-ID":"oops"}).status_code == 400
    assert client.get(url,headers={"Last-Event-ID":"-1"}).status_code == 400
    assert client.get(url+"?after=-2").status_code == 400
