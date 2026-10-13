import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
LABS = {
    "healthy": {"project": "opslens-lab-healthy", "url": "http://127.0.0.1:18181", "key_env": "HEALTHY_DIAGNOSTIC_KEY"},
    "fault": {"project": "opslens-lab-fault", "url": "http://127.0.0.1:18182", "key_env": "FAULT_DIAGNOSTIC_KEY"},
}
SERVICES = ("api", "worker", "postgres", "redis", "blocker")


def platform_dsn():
    value = os.environ.get("PLATFORM_DATABASE_URL")
    if not value:
        raise RuntimeError("Run scripts/local/Setup.ps1 first: missing platform database configuration")
    return value


def docker_executable():
    value = os.environ.get("OPSLENS_DOCKER", "")
    candidates = [
        value,
        str(Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "Programs/DockerDesktop/resources/bin/docker.exe"),
        str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker/Docker/resources/bin/docker.exe"),
    ]
    import shutil
    for p in candidates:
        try:
            if p and Path(p).is_file():return p
        except PermissionError:continue
    return shutil.which('docker') or 'docker'


def secrets():
    return [os.environ.get(k, "") for k in ("PLATFORM_DB_PASSWORD", "HEALTHY_DB_PASSWORD", "FAULT_DB_PASSWORD", "HEALTHY_DIAGNOSTIC_KEY", "FAULT_DIAGNOSTIC_KEY", "DASHSCOPE_APIKEY")]
