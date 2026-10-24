"""Explicit sidecar migration. Default is dry-run; never reparse or call a model."""
import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from opslens.caseflow.documents import migrate_legacy

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=UUID)
    parser.add_argument('--apply', action='store_true', help='Append legacy text sidecars after a verified backup')
    args = parser.parse_args()
    print(json.dumps(migrate_legacy(args.project, apply=args.apply), ensure_ascii=False))
