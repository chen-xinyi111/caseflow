import {test} from 'node:test'
import assert from 'node:assert/strict'
import {contentChanged,needsGroundingAcknowledgement,groundingLabel} from '../src/caseflowGrounding.ts'

test('edited steps and expectations invalidate displayed evidence judgement',()=>{
 const saved={title:'检查优惠',preconditions:'存在订单',data:'99.99',steps:['尝试使用'],expected:'未抵扣'}
 assert.equal(contentChanged(saved,{...saved},['尝试使用']),false)
 assert.equal(contentChanged(saved,{...saved,expected:'弹窗'},['尝试使用']),true)
 assert.equal(contentChanged(saved,{...saved},['必须登录','尝试使用']),true)
})
test('warnings and stale checks require explicit acknowledgement, legacy cases are not marked checked',()=>{
 assert.equal(groundingLabel({}),'尚未进行依据复核')
 const row={grounding:{current:true,status:'checked'}}
 assert.equal(needsGroundingAcknowledgement(row),false)
 assert.equal(needsGroundingAcknowledgement(row,true),true)
 assert.equal(needsGroundingAcknowledgement({grounding:{current:true,status:'needs_confirmation'}}),true)
 assert.equal(needsGroundingAcknowledgement({grounding:{current:false,status:'checked'}}),true)
})
