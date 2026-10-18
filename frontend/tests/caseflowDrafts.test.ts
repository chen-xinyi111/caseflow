import {test} from 'node:test'
import assert from 'node:assert/strict'
import {mergeRequirementDraft,isStaleDraft} from '../src/caseflowDrafts.ts'
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
