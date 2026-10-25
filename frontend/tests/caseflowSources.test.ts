import {test} from 'node:test'
import assert from 'node:assert/strict'
import {parseSourceRoute,highlightParts,segmentQuote,segmentSeparator,type Unit} from '../src/caseflowSources.ts'
const pid='00000000-0000-0000-0000-000000000001',did='00000000-0000-0000-0000-000000000002',eid='00000000-0000-0000-0000-000000000003'
test('explicit version and evidence survive refresh without local project preference',()=>{
 assert.deepEqual(parseSourceRoute(`#/projects/${pid}/documents/${did}?evidence=${eid}`),{projectId:pid,documentId:did,evidenceId:eid,tab:'需求'})
 assert.equal(parseSourceRoute(`#/projects/${pid}/cases`)?.tab,'用例')
 assert.equal(parseSourceRoute(''),null)
})
test('invalid links fail without falling back to a different project',()=>{
 for(const hash of ['#/bad',`#/projects/${pid}/documents/not-an-id`,`#/projects/${pid}/knowledge?evidence=${eid}`])assert.throws(()=>parseSourceRoute(hash))
})
test('highlight offsets use code points, retain repeated text and render HTML as plain text',()=>{
 const text='😀规则。规则。<script>x</script>'
 const parts=highlightParts(text,[{unit_id:'u1',start:4,end:7}],'u1')
 assert.equal(parts.map(p=>p.text).join(''),text)
 assert.equal(parts.find(p=>p.marked)?.text,'规则。')
 assert.equal(parts[0]?.text,'😀规则。')
})
test('cross-unit evidence retains explicit separation',()=>{
 const units=[{id:'u1',text:'第一段'},{id:'u2',text:'第二段'}] as Unit[]
 assert.equal(segmentQuote(units,[{unit_id:'u1',start:0,end:3},{unit_id:'u2',start:0,end:3}]),'第一段'+segmentSeparator+'第二段')
 assert.throws(()=>highlightParts('abcd',[{unit_id:'u1',start:1,end:3},{unit_id:'u1',start:2,end:4}],'u1'))
})
