import {test} from 'node:test'
import assert from 'node:assert/strict'
import {submit,recover} from '../src/api/diagnostic.ts'
const values=new Map<string,string>()
Object.defineProperty(globalThis,'localStorage',{value:{getItem:(k:string)=>values.get(k)??null,setItem:(k:string,v:string)=>values.set(k,v),removeItem:(k:string)=>values.delete(k)},configurable:true})
test('lost submission persists only identity and hash; reload reads without replay',async()=>{
 values.clear();const original=globalThis.fetch;let id='';let calls=0
 try{
  globalThis.fetch=(async (_url,options)=>{calls++;id=JSON.parse(String(options?.body)).request_id;throw new TypeError('offline')}) as typeof fetch
  await assert.rejects(submit('/diagnostic/sessions',{problem:'secret-business-log-token'}),/offline/)
  const saved=[...values.values()][0]!;assert.ok(!saved.includes('secret-business-log-token'));assert.equal(JSON.parse(saved).id,id)
  await assert.rejects(submit('/diagnostic/sessions',{problem:'changed'}),/上次提交/);assert.equal(calls,1)
  globalThis.fetch=(async (url,options)=>{assert.equal(String(url),'/api/v1/diagnostic/submissions/'+id);assert.equal(options?.method,undefined);return new Response(JSON.stringify({kind:'session',value:{id:'saved'}}))}) as typeof fetch
  Object.assign(globalThis.localStorage,Object.fromEntries(values))
  assert.deepEqual(await recover(),[{kind:'session',value:{id:'saved'}}]);assert.equal(values.size,0)
 }finally{globalThis.fetch=original;for(const k of Object.keys(localStorage))if(k.startsWith('opslens-diagnostic-pending:'))delete (localStorage as unknown as Record<string,unknown>)[k]}
})
test('definitive validation releases identity without storing raw body',async()=>{
 values.clear();const original=globalThis.fetch
 try{globalThis.fetch=(async()=>new Response(JSON.stringify({detail:'invalid'}),{status:400})) as typeof fetch;await assert.rejects(submit('/diagnostic/sessions',{problem:'raw'}),/invalid/);assert.equal(values.size,0)}finally{globalThis.fetch=original}
})

test('lost feedback reload recovers session and permits changed feedback without a replay',async()=>{
 values.clear();const original=globalThis.fetch;let id='';const path='/diagnostic/sessions/saved/feedback';let posts=0
 try{
  globalThis.fetch=(async (_url,options)=>{posts++;id=JSON.parse(String(options?.body)).request_id;throw new TypeError('lost response')}) as typeof fetch
  await assert.rejects(submit(path,{status:'paused',note:'private note'}),/lost response/)
  Object.assign(globalThis.localStorage,Object.fromEntries(values))
  globalThis.fetch=(async (url,options)=>{assert.equal(options?.method,undefined);assert.equal(String(url),'/api/v1/diagnostic/submissions/'+id);return new Response(JSON.stringify({kind:'session',value:{id:'saved',status:'paused'}}))}) as typeof fetch
  assert.equal((await recover())[0]?.kind,'session');assert.equal(posts,1);assert.equal(values.size,0)
  globalThis.fetch=(async (_url,options)=>{posts++;assert.notEqual(JSON.parse(String(options?.body)).request_id,id);return new Response(JSON.stringify({id:'saved',status:'open'}))}) as typeof fetch
  await submit(path,{status:'open',note:'changed'});assert.equal(posts,2)
 }finally{globalThis.fetch=original;for(const k of Object.keys(localStorage))if(k.startsWith('opslens-diagnostic-pending:'))delete (localStorage as unknown as Record<string,unknown>)[k]}
})


test('historical restart suggestions are conservatively labelled as state changes',async()=>{
 const {changesState}=await import('../src/api/diagnostic-risk.ts')
 assert.equal(changesState({operation:'修改第二实例的配置文件或启动参数，重新启动',changes_state:false}),true)
 assert.equal(changesState({operation:'在命令行直接运行后端启动命令（如 python main.py）',changes_state:false}),true)
 assert.equal(changesState({operation:'查看启动脚本；执行 netstat -ano',changes_state:false}),false)
})
