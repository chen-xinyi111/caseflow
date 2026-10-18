import {test} from 'node:test'
import assert from 'node:assert/strict'
import {mergeRequirementDraft,isStaleDraft,syncDocumentSelection} from '../src/caseflowDrafts.ts'
test('server updates replace untouched drafts and retain unfinished answers',()=>{
  const d=mergeRequirementDraft(undefined,'旧规则');d.answer='待提交的人工回答'
  assert.deepEqual(mergeRequirementDraft(d,'新规则'),{text:'新规则',baseText:'新规则',answer:'待提交的人工回答'})
})
test('background update preserves human text and requires reconciliation',()=>{
  const d=mergeRequirementDraft(undefined,'旧规则');d.text='人工编辑'
  const updated=mergeRequirementDraft(d,'新规则')
  assert.equal(updated.text,'人工编辑');assert.equal(isStaleDraft(updated,'新规则'),true)
  assert.equal(isStaleDraft(updated,'旧规则'),false)
})

test('new document revisions select current sources and retain other chosen references',()=>{
 const docs=[{id:'old'},{id:'reference'},{id:'new',previous_id:'old'}]
 assert.deepEqual(syncDocumentSelection(docs,['old','reference'],['old','reference']),['reference','new'])
 assert.deepEqual(syncDocumentSelection(docs,[]),['reference','new'])
})
test('refresh preserves an intentional empty scope or explicit historical selection',()=>{
 const docs=[{id:'old'},{id:'new',previous_id:'old'}]
 assert.deepEqual(syncDocumentSelection(docs,[],['old','new']),[])
 assert.deepEqual(syncDocumentSelection(docs,['old'],['old','new']),['old'])
})
