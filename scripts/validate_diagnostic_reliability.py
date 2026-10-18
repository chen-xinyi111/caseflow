"""Deterministic before/after probes; no database mutations, network or model calls."""
import json
import subprocess
from pathlib import Path
from uuid import uuid4
from opslens.diagnostic import intake,model,worker

BASELINE='27c5e92'

def historical(path):
    source=subprocess.check_output(['git','-c','safe.directory='+Path.cwd().as_posix(),'show',BASELINE+':'+path],text=True,encoding='utf-8')
    namespace={'__name__':'opslens.diagnostic.baseline','__package__':'opslens.diagnostic'}
    exec(compile(source,path,'exec'),namespace)
    return namespace

def main():
    lines=[]
    for letter in 'abcdefghijkl':lines+=['ERROR failure '+letter]+['INFO context '+'中'*1000]*19
    def source(name,text):return {'id':uuid4(),'name':name,'kind':'log','version':1,'content':text,'summary':intake.summarize(text),'provenance':{}}
    inputs=[source('large.log','\n'.join(lines)),source('root.log','ERROR ConnectionRefusedError: unique root evidence')]
    old=historical('src/opslens/diagnostic/intake.py')['segments'](inputs,[],'failure')
    new=intake.segments(inputs,[],'failure');context={'problem':'failure','sources':new}
    intake.fit_context(context,lambda:len(json.dumps(worker.messages(context,enforce=False),ensure_ascii=False).encode()))
    report={'summary':'原因待核对','facts':[{'text':'日志报错','citations':[{'id':'S1','quote':'Connection refused'}]}],
        'candidate_causes':[{'title':'地址','reason':'需要核对','citations':[]}],
        'next_checks':[{'purpose':'核对','operation':'查看配置','expected':'提供地址','changes_state':False}], 'missing_information':['监听地址']}
    original=model.dynamic_secrets;counts={}
    try:
        for label,validate in [('before',historical('src/opslens/diagnostic/worker.py')['validate_report']),('after',worker.validate_report)]:
            calls=[];model.dynamic_secrets=lambda connection=None:(calls.append(connection) or [])
            validate(report,[{'id':'S1','kind':'log','content':'Connection refused'}]);counts[label]=len(calls)
    finally:model.dynamic_secrets=original
    result={'baseline_revision':BASELINE,'fixture':'12 distinct error blocks with long UTF-8 context + one root log',
        'selected_files_before':sorted({p['name'] for p in old}),'selected_files_after':sorted({p['name'] for p in new}),
        'root_evidence_before':any('unique root evidence' in p['content'] for p in old),
        'root_evidence_after':any('unique root evidence' in p['content'] for p in new),
        'full_message_bytes_after':len(json.dumps(worker.messages(context),ensure_ascii=False).encode()),
        'extra_secret_config_reads':counts,'model_calls':0,
        'limitation':'Fixed regression fixture and logical config lookups; not quality, latency or monetary savings.'}
    assert result['root_evidence_before'] is False and result['root_evidence_after'] is True
    assert counts['before']==10 and counts['after']==1
    target=Path('.runtime/product-necessary-probes.json')
    if target.exists():raise FileExistsError('Preserve first probe; choose a new output before rerunning')
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
