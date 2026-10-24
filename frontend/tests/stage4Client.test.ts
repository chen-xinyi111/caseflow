import {test} from 'node:test'
import assert from 'node:assert/strict'
import {submitGroup,recoverGroup,groupPendingKey,stage4Request} from '../src/stage4Client.ts'
import {parseSourceRoute} from '../src/caseflowSources.ts'
const pid='00000000-0000-0000-0000-000000000001'
const values=new Map<string,string>()
Object.defineProperty(globalThis,'localStorage',{value:{getItem:(k:string)=>values.get(k)??null,setItem:(k:string,v:string)=>values.set(k,v),removeItem:(k:string)=>values.delete(k)},configurable:true})
const original=globalThis.fetch

test('lost submit response recovers exact request without another POST',async()=>{
 values.clear();let posts=0,request=''
 globalThis.fetch=(async(url:any,opts:any)=>{if(opts?.method==='POST'){posts++;request=JSON.parse(opts.body).request_id;throw Error('lost response')};assert.match(String(url),new RegExp('request_id='+request));return new Response(JSON.stringify([{id:'existing'}]))}) as typeof fetch
 try{await assert.rejects(submitGroup(pid,{kind:'prepare_basis',target:'private'}));assert.equal(values.get(groupPendingKey(pid)),request);assert.equal([...values.values()].some(v=>v.includes('private')),false);assert.equal((await submitGroup(pid,{kind:'extract_points'})).id,'existing');assert.equal(posts,1);assert.equal(values.size,0)}finally{globalThis.fetch=original}
})
test('unknown result is retained when exact request is still absent',async()=>{
 values.clear();values.set(groupPendingKey(pid),'pending');let calls=0
 globalThis.fetch=(async()=>{calls++;return new Response('[]')}) as typeof fetch
 try{assert.equal(await recoverGroup(pid),null);await assert.rejects(submitGroup(pid,{}),/不会另建/);assert.equal(values.get(groupPendingKey(pid)),'pending');assert.equal(calls,2)}finally{globalThis.fetch=original}
})
test('definitive rejection clears pending id and retains per-item errors',async()=>{
 values.clear();globalThis.fetch=(async()=>new Response(JSON.stringify({detail:{message:'本批未保存',items:[{id:'tp',reason:'旧依据'}]}}),{status:409})) as typeof fetch
 try{await assert.rejects(submitGroup(pid,{}),(e:any)=>e.status===409&&e.items[0].id==='tp');assert.equal(values.size,0);await assert.rejects(stage4Request('/anything'),/本批未保存/)}finally{globalThis.fetch=original}
})
test('test-point review route survives refresh with explicit project',()=>assert.equal(parseSourceRoute('#/projects/'+pid+'/points')?.tab,'测试点'))
