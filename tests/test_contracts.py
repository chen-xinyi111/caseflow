import pytest
from pydantic import ValidationError

from opslens.contracts import JobInput, LabName
from opslens.security import redact, validate_target
from lab.processing import count_text


def test_text_count_oracle_unicode_and_whitespace():
    assert count_text("OpsLens AI makes evidence clear") == {"char_count": 31, "word_count": 5}
    assert count_text("你好 世界\n a\t b ") == {"char_count": 12, "word_count": 4}
    assert count_text("  ") == {"char_count": 2, "word_count": 0}


def test_inputs_reject_blank_oversized_and_unknown_lab():
    from uuid import uuid4
    for text in ("", "  \n", "x" * 10001):
        with pytest.raises(ValidationError):
            JobInput(text=text, request_id=uuid4())
    with pytest.raises(ValueError):
        LabName("qinghe-after-sales")


def test_redaction_covers_connections_secrets_and_authorization():
    value = "postgresql://alice:local-password@postgres/db\nREDIS_PASSWORD=secret-value\nAuthorization: Bearer abc123\napi_key='model-secret'"
    cleaned = redact(value, ["local-password", "secret-value", "model-secret"])
    for secret in ("local-password", "secret-value", "abc123", "model-secret"):
        assert secret not in cleaned
    assert "[REDACTED]" in cleaned


def test_docker_target_must_match_project_service_and_scope():
    valid = {"com.docker.compose.project": "opslens-lab-fault", "com.docker.compose.service": "worker", "io.opslens.scope": "lab"}
    validate_target(valid, LabName.fault, "worker")
    for key, replacement in (("com.docker.compose.project", "agent-commerce-stage3"), ("com.docker.compose.service", "kafka"), ("io.opslens.scope", "other")):
        with pytest.raises(ValueError):
            validate_target(valid | {key: replacement}, LabName.fault, "worker")
