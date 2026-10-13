import { request } from './client.ts'

export type Pending = { path: string; body: Record<string, unknown> }
const storageKey = (path:string) => 'opslens-pending:'+path
export function pending(path:string):Pending|null {
  try { return JSON.parse(localStorage.getItem(storageKey(path))??'null') as Pending|null }
  catch { return null }
}
export async function durableRequest<T>(path:string,body:Record<string,unknown>):Promise<T> {
  const input={...body};delete input.request_id
  const old=pending(path)
  if(old){
    const previous={...old.body};delete previous.request_id
    if(JSON.stringify(previous)!==JSON.stringify(input))throw new Error('上次提交结果尚未确认，请先重新连接找回原任务')
  }
  const saved=old??{path,body:{...input,request_id:crypto.randomUUID()}}
  localStorage.setItem(storageKey(path),JSON.stringify(saved))
  try {
    const value=await request<T>(path,saved.body)
    localStorage.removeItem(storageKey(path))
    return value
  } catch(e) {
    // Keep uncertain responses. Validation failures are definitive and may be corrected.
    if(e instanceof Error && 'status' in e && Number(e.status)>=400 && Number(e.status)<500)localStorage.removeItem(storageKey(path))
    throw e
  }
}
export async function resumePending<T>(path:string):Promise<T|null> {
  const saved=pending(path)
  return saved?durableRequest<T>(path,saved.body):null
}
export function eventUrl(path:string):string {
  const after=localStorage.getItem('opslens-cursor:'+path)??'0'
  return '/api/v1'+path+'?after='+encodeURIComponent(after)
}
export function saveCursor(path:string,event:MessageEvent):void {
  if(/^\d+$/.test(event.lastEventId))localStorage.setItem('opslens-cursor:'+path,event.lastEventId)
}
