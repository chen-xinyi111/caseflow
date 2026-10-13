import hashlib,json,subprocess
from pathlib import Path
from opslens.config import ROOT
PATH=ROOT/'data/evaluation/frozen-v1.json'
MANIFEST=ROOT/'data/evaluation/manifest-v1.json'
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def validate_dataset(data):
    for kind,count,field in [('retrieval',60,'query'),('investigation',30,'question')]:
        rows=data[kind]
        if len(rows)!=count*2 or any(sum(r['split']==s for r in rows)!=count for s in ('dev','test')):raise ValueError('评测样本数量或分组错误')
        if len({r['id'] for r in rows})!=len(rows):raise ValueError('案例编号重复')
        # Investigation intentionally has matched question variants across source groups.
        keys=[(r.get('group') if kind=='investigation' else '',r[field]) for r in rows]
        if len(set(keys))!=len(keys):raise ValueError('同来源输入重复')
        if {r['group'] for r in rows if r['split']=='dev'} & {r['group'] for r in rows if r['split']=='test'}:raise ValueError('来源分组跨集合泄漏')
    return data
def load_dataset():return validate_dataset(json.loads(PATH.read_text(encoding='utf-8')))
def verify_freeze():
    data=load_dataset();manifest=json.loads(MANIFEST.read_text(encoding='utf-8'))
    if digest(data)!=manifest['dataset_sha256']:raise ValueError('冻结数据集发生变化')
    for name,expected in manifest['files'].items():
        # Historical evaluation implementations remain immutable after product upgrades.
        # New executions independently seal the active implementations in store.seal().
        historical=ROOT/'data/evaluation/source-v1'/name
        source=historical if name.startswith('src/') and historical.is_file() else ROOT/name
        if hashlib.sha256(source.read_bytes()).hexdigest()!=expected:raise ValueError('冻结来源或实现已变化：'+name)
    if (ROOT/'data/evaluation/source-v1').is_dir():manifest=manifest|{'baseline_source_storage':'data/evaluation/source-v1; active execution files sealed separately'}
    return manifest
def freeze():
    from opslens.evaluation.catalog import authored_dataset
    if PATH.exists() or MANIFEST.exists():raise ValueError('冻结文件已存在，不能覆盖')
    data=validate_dataset(authored_dataset());PATH.parent.mkdir(parents=True,exist_ok=True)
    files=list((ROOT/'data/knowledge').glob('*.md'))+[ROOT/p for p in ['src/lab/app.py','src/lab/worker.py','src/lab/common.py','src/opslens/investigation/engine.py','src/opslens/investigation/collaboration.py','src/opslens/investigation/verification.py','src/opslens/rag/ranking.py']]
    manifest={'version':data['version'],'dataset_sha256':digest(data),'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'files':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files},'retrieval_group_counts':{'dev':4,'test':5},'investigation_group_counts':{'dev':3,'test':3},'parameters':{'return_k':6,'recall_k':20,'model_calls':8,'tool_calls':12,'subtasks':6,'timeout_seconds':240},'limits':['手册与故障机制均已用于历史开发；本次只冻结新的输入变体','调查每个故障组复用同一真实快照，组内样本相关，不能视为独立故障','相关性为单个手册来源标注，不是逐段语义金标','根因规则与引文存在性不等于人工语义审核']}
    PATH.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    MANIFEST.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
if __name__=='__main__':freeze()
