import type { components } from './schema'
export type LabStatus = components['schemas']['LabStatus']
export type LabJob = components['schemas']['LabJob']
export type FaultCase = components['schemas']['FaultCase']
export type Evidence = components['schemas']['Evidence']
export type Collection = components['schemas']['Collection']
export async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch('/api/v1' + path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const data = await response.json()
  if (!response.ok) throw Object.assign(new Error(typeof data.detail === 'string' ? data.detail : '请求未通过校验，请检查输入'),{status:response.status})
  return data as T
}
export function sourceName(value: string): string {
  const names: Record<string,string> = { 'docker.services': '容器运行状态', 'docker.config_summary': '配置与版本摘要', 'lab.diagnostics': '业务与依赖诊断' }
  return names[value] ?? value.replace('docker.logs.', '') + ' 运行日志'
}
