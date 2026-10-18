import {test} from 'node:test'
import assert from 'node:assert/strict'
import {nextStep,unreadDocuments,updatedDrafts,type WorkflowState} from '../src/caseflowWorkflow.ts'
const empty=():WorkflowState=>({documents:[],requirements:[],cases:[],changes:[]})
test('new real projects start with input and saving a document leads to reading',()=>{
 const s=empty();assert.equal(nextStep(s,[]).target,'import')
 s.documents=[{id:'d',role:'requirement'}];assert.equal(nextStep(s,[]).target,'scope')
 assert.equal(nextStep(s,[{kind:'extract',status:'running'}]).action,'')
})
test('replacement documents stay unread until their own extraction completes',()=>{
 const s=empty();s.documents=[{id:'old',role:'requirement'},{id:'new',role:'requirement',previous_id:'old'}]
 s.requirements=[{id:'r',document_id:'old',status:'confirmed'}]
 assert.deepEqual(unreadDocuments(s,[]).map(d=>d.id),['new'])
 assert.deepEqual(unreadDocuments(s,[{kind:'extract',status:'completed',input:{document_ids:['new']}}]),[])
})
test('updated rules are confirmed in the change workspace before remaining candidates',()=>{
 const s=empty();s.documents=[{id:'d',role:'requirement'}];s.requirements=[{id:'r',document_id:'d',status:'draft'}]
 s.changes=[{id:'done',status:'accepted',decision:'replace',selected_ids:['r']},{id:'pending',status:'pending'}]
 assert.equal(updatedDrafts(s).length,1);assert.equal(nextStep(s,[]).target,'updated')
 s.requirements[0]!.status='confirmed';assert.equal(nextStep(s,[]).target,'changes')
 s.requirements.push({id:'new-rule',document_id:'d',status:'draft',text:'新增规则'})
 s.changes.push({id:'added',status:'accepted',decision:'add',document_id:'d',proposed:{text:'新增规则'}})
 assert.equal(nextStep(s,[]).target,'updated')
})
test('a stale checklist takes priority over delivery while applied proposals are ignored',()=>{
 const s=empty();s.documents=[{id:'d',role:'requirement'}];s.requirements=[{id:'r',document_id:'d',status:'confirmed'}]
 s.cases=[{id:'approved',status:'approved'},{id:'old',status:'needs_review'},{id:'proposal',status:'draft',applied_to:'old'}]
 assert.equal(nextStep(s,[]).target,'review')
 s.cases[1]!.status='approved';assert.equal(nextStep(s,[]).target,'export')
})
