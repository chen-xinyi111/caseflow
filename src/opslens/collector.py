import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
import httpx
from opslens.config import LABS, SERVICES, docker_executable, secrets
from opslens.security import redact, validate_target


def docker(args, timeout=12):
    result = subprocess.run([docker_executable(), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False)
    if result.returncode:
        raise RuntimeError(redact(result.stderr.strip()[:1500], secrets()))
    return result.stdout + (result.stderr if args and args[0] == "logs" else "")


def containers(lab):
    if lab not in LABS:
        raise ValueError("未知实验实例")
    ids = docker(["ps", "-aq", "--filter", "label=com.docker.compose.project=" + LABS[lab]["project"]]).split()
    rows = []
    for container_id in ids:
        obj = json.loads(docker(["inspect", container_id]))[0]
        service = obj["Config"]["Labels"].get("com.docker.compose.service")
        validate_target(obj["Config"]["Labels"], lab, service)
        rows.append((container_id, service, obj))
    if not rows:
        raise RuntimeError("实验容器尚未启动")
    return rows


def service_states(rows):
    return [{"service": service, "state": obj["State"]["Status"], "health": obj["State"].get("Health", {}).get("Status")} for _, service, obj in rows]


def diagnostics(lab):
    cfg = LABS[lab]
    with httpx.Client(timeout=5, trust_env=False) as client:
        response = client.get(cfg["url"] + "/diagnostics", headers={"X-OpsLens-Key": os.environ[cfg["key_env"]]})
        response.raise_for_status()
        data = response.json()
    if data.get("instance") != lab:
        raise ValueError("诊断实例身份不匹配")
    return data


def lab_status(lab):
    timestamp = datetime.now(timezone.utc)
    errors = []
    try:
        states = service_states(containers(lab))
    except Exception as exc:
        states = []
        errors.append(redact(str(exc), secrets()))
    try:
        diag = diagnostics(lab)
    except Exception as exc:
        diag = {}
        errors.append(redact(str(exc), secrets()))
    return {"lab": lab, "collected_at": timestamp, "services": states, "diagnostics": diag, "collection_error": "; ".join(errors) or None}


def collect(lab, sink=None, skip=None):
    items = []
    skip=set(skip or [])
    def add(source, success, value):
        if source in skip:return
        content = redact(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str, indent=2), secrets())
        items.append({"lab": lab, "source": source, "collected_at": datetime.now(timezone.utc), "success": success, "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()})
        if sink:sink(items[-1])
    try:
        rows = containers(lab)
        add("docker.services", True, service_states(rows))
        for container_id, service, obj in rows:
            if "docker.logs."+service in skip:continue
            try:
                add("docker.logs." + service, True, docker(["logs", "--timestamps", "--tail", "80", container_id]))
            except Exception as exc:
                add("docker.logs." + service, False, str(exc))
        configs = []
        for _, service, obj in rows:
            allowed = ("LAB_INSTANCE=", "LAB_WORKER_VERSION=", "LAB_REDIS_URL=")
            configs.append({"service": service, "image": obj["Config"]["Image"], "environment": [redact(v, secrets()) for v in obj["Config"].get("Env", []) if v.startswith(allowed)]})
        add("docker.config_summary", True, configs)
    except Exception as exc:
        add("docker.services", False, str(exc))
    try:
        if "lab.diagnostics" not in skip:add("lab.diagnostics", True, diagnostics(lab))
    except Exception as exc:
        add("lab.diagnostics", False, str(exc))
    return items
