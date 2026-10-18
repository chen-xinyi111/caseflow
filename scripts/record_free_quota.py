"""Record a human-verified Bailian quota. Does not verify, buy, or enable a model."""
import argparse,hashlib,json,os,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from opslens.rag.provider import api_base

def main():
    p=argparse.ArgumentParser(description='仅在已登录控制台核对额度和用完即停后登记；不得凭估计填写')
    p.add_argument('--model',required=True,choices=['qwen3.8-max','qwen3.8-max-0902','deepseek-v4.1-flash','text-embedding-v4','gte-rerank-v2'])
    p.add_argument('--remaining',required=True,type=int,help='控制台所见剩余额度，向下取整')
    p.add_argument('--expires',required=True,help='控制台所见到期日 YYYY-MM-DD')
    p.add_argument('--evidence',required=True,type=Path,help='本机保存的当前控制台截图，不上传仓库')
    p.add_argument('--confirmed-free-only',required=True,action='store_true',help='已在当前账户确认免费用完即停开启')
    a=p.parse_args();key=os.environ.get('DASHSCOPE_APIKEY','');now=datetime.now(timezone.utc)
    expiry=datetime.fromisoformat(a.expires+'T00:00:00+08:00')
    if not key or a.remaining<=0 or expiry<=now or not a.evidence.is_file():p.error('需要有效服务端密钥、正数额度、未来到期日和真实截图')
    stamp=now.isoformat();fingerprint=hashlib.sha256(key.encode()).hexdigest();endpoint=api_base()
    path=ROOT/'.runtime/model-quota.json';path.parent.mkdir(exist_ok=True)
    record=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'models':{}}
    if record.get('key_sha256',fingerprint)!=fingerprint or record.get('api_base',endpoint)!=endpoint:record={'models':{}}
    record.update(key_sha256=fingerprint,api_base=endpoint)
    record['models'][a.model]={'region':'cn-beijing','free_only_effective':True,'remaining_tokens':a.remaining,'verified_at':stamp,'expires_at':expiry.isoformat(),'verification_source':'Human-confirmed authenticated Bailian Beijing console; evidence sha256='+hashlib.sha256(a.evidence.read_bytes()).hexdigest()}
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(record,indent=2),encoding='utf-8');temporary.replace(path)
    print('已登记本机核验，24小时后失效；未启用模型。控制台用完即停仍须保持开启。')
if __name__=='__main__':main()
