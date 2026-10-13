import {test} from 'node:test'
import assert from 'node:assert/strict'
import {durableRequest,pending,resumePending,eventUrl,saveCursor} from '../src/api/durable.ts'

const values=new Map<string,string>()
Object.defineProperty(globalThis,'localStorage',{value:{getItem:(k:string)=>values.get(k)??null,setItem:(k:string,v:string)=>values.set(k,v),removeItem:(k:string)=>values.delete(k)},configurable:true})

test('a lost response and reload reuse the exact original request id',async()=>{
  values.clear();const original=globalThis.fetch;let id='';let calls=0
  try{
    globalThis.fetch=(async (_url,options)=>{
      const body=JSON.parse(String(options?.body));calls++
      if(!id)id=body.request_id
      assert.equal(body.request_id,id)
      if(calls===1)throw new TypeError('connection lost')
      return new Response(JSON.stringify({id:'saved-task'}))
    }) as typeof fetch
    await assert.rejects(durableRequest('/investigations',{question:'worker',target:'fault'}),/connection lost/)
    assert.equal(pending('/investigations')?.body.request_id,id)
    assert.deepEqual(await resumePending('/investigations'),{id:'saved-task'})
    assert.equal(pending('/investigations'),null)
  }finally{globalThis.fetch=original}
})

test('uncertain submission cannot be replaced with different input',async()=>{
  values.clear();const original=globalThis.fetch
  try{
    globalThis.fetch=(async()=>{throw new TypeError('offline')}) as typeof fetch
    await assert.rejects(durableRequest('/kb/searches',{query:'old'}))
    await assert.rejects(durableRequest('/kb/searches',{query:'new'}),/上次提交结果尚未确认/)
    assert.equal(pending('/kb/searches')?.body.query,'old')
  }finally{globalThis.fetch=original}
})

test('definitive validation errors release the pending request',async()=>{
  values.clear();const original=globalThis.fetch
  try{
    globalThis.fetch=(async()=>new Response(JSON.stringify({detail:'invalid'}),{status:400})) as typeof fetch
    await assert.rejects(durableRequest('/collections',{lab:'wrong'}),/invalid/)
    assert.equal(pending('/collections'),null)
  }finally{globalThis.fetch=original}
})

test('progress resumes after the last persisted event',()=>{
  values.clear();const path='/investigations/a/events'
  assert.equal(eventUrl(path),'/api/v1'+path+'?after=0')
  saveCursor(path,{lastEventId:'27'} as MessageEvent)
  assert.equal(eventUrl(path),'/api/v1'+path+'?after=27')
  saveCursor(path,{lastEventId:'bad'} as MessageEvent)
  assert.equal(eventUrl(path),'/api/v1'+path+'?after=27')
})
