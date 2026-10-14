import {request} from './client.ts'

type Submission={id:string,hash:string,path:string}
const key=(path:string)=>'opslens-diagnostic-pending:'+path
async function fingerprint(value:unknown){const bytes=new TextEncoder().encode(JSON.stringify(value));return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(b=>b.toString(16).padStart(2,'0')).join('')}
export async function submit<T>(path:string,body:Record<string,unknown>):Promise<T>{
 const hash=await fingerprint(body)
 const old=JSON.parse(localStorage.getItem(key(path))??'null') as Submission|null
 if(old&&old.hash!==hash)throw new Error('上次提交尚未确认，请先找回记录或恢复原输入')
 const pending=old??{id:crypto.randomUUID(),hash,path};localStorage.setItem(key(path),JSON.stringify(pending))
 if(old)try{const r=await request<{value:T}>('/diagnostic/submissions/'+old.id);localStorage.removeItem(key(path));return r.value}catch(e){if(!(e instanceof Error&&'status' in e&&Number(e.status)===404))throw e}
 try{const result=await request<T>(path,{...body,request_id:pending.id});localStorage.removeItem(key(path));return result}
 catch(e){if(e instanceof Error&&'status' in e&&Number(e.status)>=400&&Number(e.status)<500)localStorage.removeItem(key(path));throw e}
}
export async function upload<T>(path:string,files:File[],kind:string):Promise<T>{
 const signatures=await Promise.all(files.map(async f=>({name:f.name,bytes:f.size,sha:Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await f.arrayBuffer()))).map(b=>b.toString(16).padStart(2,'0')).join('')})))
 const hash=await fingerprint({kind,signatures});const old=JSON.parse(localStorage.getItem(key(path))??'null') as Submission|null
 if(old&&old.hash!==hash)throw new Error('上次上传尚未确认，请先重新加载排查记录')
 const pending=old??{id:crypto.randomUUID(),hash,path};localStorage.setItem(key(path),JSON.stringify(pending))
 const data=new FormData();data.append('request_id',pending.id);data.append('kind',kind);for(const f of files)data.append('files',f)
 try{const response=await fetch('/api/v1'+path,{method:'POST',body:data});const result=await response.json();if(!response.ok){if(response.status<500)localStorage.removeItem(key(path));throw new Error(typeof result.detail==='string'?result.detail:'上传失败')}localStorage.removeItem(key(path));return result as T}catch(e){throw e}
}
export async function recover(){
 const recovered:Array<{kind:string,value:unknown}>=[]
 const keys=Object.keys(localStorage).filter(k=>k.startsWith('opslens-diagnostic-pending:'))
 for(const k of keys){try{const pending=JSON.parse(localStorage.getItem(k)!) as Submission;const result=await request<{kind:string,value:unknown}>('/diagnostic/submissions/'+pending.id);recovered.push(result);localStorage.removeItem(k)}catch{/* Keep unresolved ids without replaying bodies or model requests. */}}
 return recovered
}
