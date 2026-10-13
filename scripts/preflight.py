"""Run a read-only resource and identity check before startup."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from opslens.deployment import inspect_environment
from opslens.security import redact
from opslens.config import secrets
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--scope',choices=['Platform','Fault','All'],default='Platform');args=parser.parse_args()
    try:report=inspect_environment(args.scope)
    except Exception as e:
        print('预检未通过：'+redact(str(e),secrets())[:800]);sys.exit(1)
    (ROOT/'.runtime').mkdir(exist_ok=True)
    (ROOT/'.runtime/preflight.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for item in report['checks']:print(('通过：' if item['ok'] else '未通过：')+item['name']+' — '+item['message'])
    sys.exit(0 if report['ok'] else 1)
