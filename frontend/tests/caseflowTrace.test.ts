import {test} from 'node:test'
import assert from 'node:assert/strict'
import {freezePointSelection,expansionBindings,freezeCaseSelection,caseReviewValues} from '../src/caseflowTrace.ts'
import {needsGroundingAcknowledgement} from '../src/caseflowGrounding.ts'
const row={id:'00000000-0000-0000-0000-000000000001',revision:2,dependency_fingerprint:'a'.repeat(64),evidence_ids:['e1']}
test('selection remains the explicitly chosen revision when server data updates',()=>{
 const source=structuredClone(row),selected=freezePointSelection(source);source.revision=3;source.dependency_fingerprint='b'.repeat(64);source.evidence_ids.push('e2')
 assert.deepEqual(expansionBindings([selected]),[{id:row.id,revision:2,fingerprint:'a'.repeat(64)}]);assert.deepEqual(selected.evidence_ids,['e1'])
 assert.throws(()=>expansionBindings([selected,selected]));assert.throws(()=>freezePointSelection({...row,dependency_fingerprint:''}))
})
test('case selection and review never substitute a newer binding silently',()=>{
 const source={...row,trace_mode:'point',binding_fingerprint:'c'.repeat(64)};const selected=freezeCaseSelection(source);source.revision=3
 assert.equal(selected.case_revision,2)
 const values=caseReviewValues(source,{fields:['preconditions','data','steps','expected'],note:'核对当前证据'})
 assert.equal(values.binding_fingerprint,'c'.repeat(64));assert.equal(values.acknowledge_grounding,true)
})
test('four repeated fields or a global checkbox do not complete point-case manual review',()=>{
 const source={...row,trace_mode:'point',binding_fingerprint:'c'.repeat(64)}
 assert.equal(caseReviewValues(source,{fields:[],note:'全局确认'},true).acknowledge_grounding,false)
 assert.equal(caseReviewValues(source,{fields:['data','data','data','data'],note:'重复字段'},true).acknowledge_grounding,false)
 assert.deepEqual(caseReviewValues({...source,trace_mode:'legacy_direct'},{fields:[],note:''},true),{acknowledge_grounding:true})
})
test('new point cases without a current model report require review while legacy remains compatible',()=>{
 assert.equal(needsGroundingAcknowledgement({trace_mode:'point'}),true)
 assert.equal(needsGroundingAcknowledgement({trace_mode:'legacy_direct'}),false)
 assert.equal(needsGroundingAcknowledgement({trace_mode:'point',trace_current:false,grounding:{current:true,status:'checked'}}),true)
})
