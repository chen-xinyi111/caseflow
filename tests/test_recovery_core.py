import pytest
from pydantic import ValidationError
from opslens.recovery.contracts import Propose, Approve
from opslens.recovery.policy import eligible, digest, require_current


def state(worker='exited',redis_url='redis://redis:6379/0',version='v1'):
    return {'bindings':{'worker':{'id':'w','state':worker,'health':None,'environment':{'LAB_REDIS_URL':redis_url,'LAB_WORKER_VERSION':version}},
      'api':{'id':'a','state':'running','health':'healthy'},'postgres':{'id':'p','state':'running','health':'healthy'},'redis':{'id':'r','state':'running','health':'healthy'},'blocker':{'id':'b','state':'exited'}},
      'dependencies':{'database':True,'redis':True,'injected_lock':False},'artifacts':{'compose':'fixed','env':'fixed'}}


def test_recovery_inputs_reject_arbitrary_command_target_and_unconfirmed_approval():
    from uuid import uuid4
    for body in ({'action':'shell'},{'action':'start_worker','target':'healthy'},{'action':'start_worker','command':'rm -rf /'}):
        with pytest.raises(ValidationError):Propose(request_id=uuid4(),investigation_id=uuid4(),**body)
    with pytest.raises(ValidationError):Approve(request_id=uuid4(),version=1,digest='a'*64,confirm=False)


def test_whitelist_eligibility_preserves_dependency_and_saved_config_checks():
    assert eligible(state(),'start_worker')
    assert not eligible(state(worker='running'),'start_worker')
    assert not eligible(state(redis_url='redis://outside/0'),'start_worker')
    assert eligible(state(redis_url='redis://missing-opslens-redis.invalid:6379/0'),'restore_worker_config')
    assert eligible(state(worker='running',version='v0-bug'),'rollback_worker_version')
    assert not eligible(state(version='unknown'),'rollback_worker_version')
    s=state();s['dependencies']['redis']=False
    assert not eligible(s,'start_worker')
    s=state();s['bindings']['blocker']['state']='running';s['dependencies']['injected_lock']=True
    assert eligible(s,'release_injected_blocker')


def test_preexecution_fingerprint_rejects_state_container_or_saved_config_changes():
    s=state();assert require_current(s,s,'start_worker') is None
    for change in ('state','id','artifacts'):
        value=state()
        if change=='artifacts':value['artifacts']['compose']='tampered'
        else:value['bindings']['worker'][change]='changed'
        with pytest.raises(ValueError):require_current(s,value,'start_worker')
    assert digest({'version':1,'action':'start_worker'})!=digest({'version':2,'action':'start_worker'})


def test_adapter_uses_only_fixed_start_and_saved_compose_commands(monkeypatch):
    from opslens.recovery import adapter as module
    calls=[];a=module.Adapter();s=state()
    monkeypatch.setattr(a,'snapshot',lambda:s)
    monkeypatch.setattr(module,'docker',lambda args,**kwargs:calls.append(args) or 'ok')
    a.mutate('start_worker',s);assert calls==[['start','w']]
    s=state(redis_url='redis://missing-opslens-redis.invalid:6379/0')
    a.mutate('restore_worker_config',s)
    command=calls[-1]
    assert command[:4]==['compose','-p','opslens-lab-fault','--env-file']
    assert command[-2:]==['never','worker'] and '--no-deps' in command and '--no-build' in command
    assert not any(x in command for x in ('down','rm','exec','run','build','--volumes'))
    with pytest.raises(ValueError):a.mutate('shell',s)
    assert len(calls)==2


def test_saved_compose_tampering_cannot_reach_docker(tmp_path,monkeypatch):
    from opslens.recovery import adapter as module
    (tmp_path/'compose').mkdir();(tmp_path/'compose/lab.yaml').write_text('malicious definition')
    monkeypatch.setattr(module,'ROOT',tmp_path)
    with pytest.raises(ValueError,match='被修改'):module.Adapter().snapshot()
