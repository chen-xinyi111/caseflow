from pathlib import Path
from uuid import uuid4
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from opslens.demo import Demo, read_demo
from opslens.api import app

def test_demo_unavailable_has_actionable_reason_and_no_fake_ids(tmp_path):
    row=read_demo(tmp_path/'missing.json')
    assert not row.available and row.success_id is None and 'Demo.ps1' in row.reason

def test_demo_manifest_only_accepts_controlled_source_and_uuid_ids():
    with pytest.raises(ValidationError):Demo(available=True,response_source='real-model')
    with pytest.raises(ValidationError):Demo(available=True,success_id='not-a-task')

def test_demo_get_is_readonly_and_no_preparation_endpoint(monkeypatch):
    from opslens import demo
    monkeypatch.setattr(demo,'read_demo',lambda:Demo(available=False,reason='尚未准备'))
    c=TestClient(app,raise_server_exceptions=False)
    assert c.get('/api/v1/demo').json()['available'] is False
    assert c.post('/api/v1/demo',json={}).status_code==405
