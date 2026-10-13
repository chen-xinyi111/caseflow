"""Scan working files, staged blobs and committed blobs; never print secrets."""
import argparse
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ('.runtime/', '.archives/', '.venv/', 'frontend/node_modules/',
             'frontend/dist/', 'docs/reports/', 'docs/specs/', 'docs/ACCEPTANCE-')
PRIVATE_DOCS = {'docs/PROJECT_PLAN.md', 'docs/RESUME.md', 'docs/RETROSPECTIVE.md',
                'docs/EVALUATION-DATA-3.2.md', 'scripts/write_delivery_report.py'}
PATTERNS = {
    'credential token': re.compile(rb'(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})'),
    'private key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'personal Windows path': re.compile(rb'(?i)[a-z]:[\\/]Users[\\/](?!Public(?:[\\/]|$)|Default(?:[\\/]|$))[^\\/\s"\']+[\\/]'),
    'literal database password': re.compile(rb'(?:postgres(?:ql)?|mysql|redis)://[^\s/@:"\']+:([^\s/@"\']+)@'),
}
# Exact synthetic inputs used by redaction tests. Known-secret checks still apply.
FIXTURES = {
    'tests/test_contracts.py': {'literal database password': {b'postgresql' + b'://alice:local-password@'}},
    'tests/test_external.py': {'literal database password': {b'redis' + b'://user:pass@'}},
    'tests/test_demo_review.py': {'personal Windows path': {b'C:' + b'\\Users\\person\\'}},
    'tests/test_rag_provider.py': {'personal Windows path': {b'C:' + b'/Users/person/'}},
}

def git(*args):
    return subprocess.check_output(['git', '-c', f'safe.directory={ROOT.as_posix()}', *args], cwd=ROOT)

def names(raw):
    return [x for x in raw.decode('utf-8').split('\0') if x]

def known_secrets(env_file):
    keys = ('DASHSCOPE_APIKEY', 'PLATFORM_DB_PASSWORD', 'HEALTHY_DB_PASSWORD',
            'FAULT_DB_PASSWORD', 'HEALTHY_DIAGNOSTIC_KEY', 'FAULT_DIAGNOSTIC_KEY')
    values = {os.environ.get(k, '') for k in keys}
    if env_file:
        for line in Path(env_file).read_text(encoding='utf-8').splitlines():
            key, sep, value = line.partition('=')
            if sep and key in keys:
                values.add(value.strip().strip('"\''))
    return [v.encode('utf-8') for v in values if len(v) >= 8]

def check(path, content, known):
    findings = []
    parts = Path(path).parts
    if path.startswith(FORBIDDEN) or path in PRIVATE_DOCS or any(p in ('__pycache__', '.pytest_cache') for p in parts):
        findings.append('private or generated file')
    if path == '.env' or (Path(path).name.startswith('.env.') and Path(path).name != '.env.example') or Path(path).suffix.lower() in ('.pem', '.key', '.db', '.dump'):
        findings.append('local configuration or private artifact')
    if any(secret in content for secret in known):
        findings.append('known local secret')
    for label, pattern in PATTERNS.items():
        allowed = FIXTURES.get(path, {}).get(label, set())
        matches = [m for m in pattern.finditer(content) if m.group(0) not in allowed]
        if label == 'literal database password':
            matches = [m for m in matches if not any(marker in m.group(1) for marker in (b'$', b'{', b'<', b'%'))]
        if matches:
            findings.append(label)
    return findings

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('working', 'staged', 'commit'), default='working')
    parser.add_argument('--ref', default='HEAD', help='Commit to inspect in commit mode')
    parser.add_argument('--known-env', help='Optional private local env file; values remain in memory')
    args = parser.parse_args()
    if args.mode == 'working':
        paths = names(git('ls-files', '--cached', '--others', '--exclude-standard', '-z'))
        read = lambda p: (ROOT / p).read_bytes()
    elif args.mode == 'staged':
        paths = names(git('ls-files', '-z'))
        read = lambda p: git('show', ':' + p)
    else:
        paths = names(git('ls-tree', '-r', '--name-only', '-z', args.ref))
        read = lambda p: git('show', args.ref + ':' + p)
    known = known_secrets(args.known_env)
    bad = []
    for path in sorted(set(paths)):
        findings = check(path, read(path), known)
        if findings:
            bad.append((path, findings))
    for path, reasons in bad:
        print(path + ': ' + ', '.join(reasons))
    if bad:
        raise SystemExit(1)
    print(f'Publication {args.mode} check passed ({len(set(paths))} files).')

if __name__ == '__main__':
    main()
