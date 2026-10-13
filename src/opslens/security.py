import re
from opslens.config import LABS, SERVICES


def redact(text: str, known_secrets=()) -> str:
    value = text
    for secret in sorted((s for s in known_secrets if s), key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(\b(?:postgres(?:ql)?|redis)://[^\s:/]+:)[^\s@]+@", r"\1[REDACTED]@", value, flags=re.I)
    value = re.sub(r"(authorization\s*:\s*(?:bearer|basic)\s+)[^\s]+", r"\1[REDACTED]", value, flags=re.I)
    value = re.sub(r"""((?<![\w-])(?:[\w-]*(?:password|secret|api[_-]?key|token))['"]?\s*[=:]\s*['"]?)[^\s,'";}]+""", r"\1[REDACTED]", value, flags=re.I)
    return value


def validate_target(labels, lab, service):
    if service not in SERVICES or lab not in LABS:
        raise ValueError("目标不在实验白名单内")
    expected = {
        "com.docker.compose.project": LABS[lab]["project"],
        "com.docker.compose.service": service,
        "io.opslens.scope": "lab",
    }
    if any(labels.get(k) != v for k, v in expected.items()):
        raise ValueError("目标容器身份与独立实验环境不符")
