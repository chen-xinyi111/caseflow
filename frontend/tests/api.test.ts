import { test } from 'node:test'
import assert from 'node:assert/strict'
import { request, sourceName } from '../src/api/client.ts'

test('errors remain visible and requests stay on the platform API', async () => {
 const original = globalThis.fetch
 try{
  globalThis.fetch = (async (url) => {
   assert.equal(url,'/api/v1/labs')
   return new Response(JSON.stringify({detail:'实验服务暂时不可达'}),{status:503})
  }) as typeof fetch
  await assert.rejects(request('/labs'),/实验服务暂时不可达/)
 }finally{globalThis.fetch=original}
})
test('evidence titles preserve unknown sources',()=>{
 assert.equal(sourceName('docker.services'),'容器运行状态')
 assert.equal(sourceName('docker.logs.worker'),'worker 运行日志')
})
