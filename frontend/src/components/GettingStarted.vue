<script setup lang="ts">
import {ref,onMounted} from 'vue'
import {request} from '../api/client'
import type {components} from '../api/schema'
type Demo=components['schemas']['Demo']
const emit=defineEmits<{navigate:[section:string]}>()
const demo=ref<Demo|null>(null),error=ref('')
async function refresh(){try{demo.value=await request<Demo>('/demo');error.value=''}catch(e){error.value=e instanceof Error?e.message:String(e)}}
function investigation(id:string|null|undefined){if(!id)return;localStorage.setItem('opslens-investigation-last',id);emit('navigate','investigation')}
function recovery(){if(!demo.value?.success_id)return;localStorage.setItem('opslens-recovery-investigation',demo.value.success_id);emit('navigate','recovery')}
onMounted(refresh)
</script>
<template>
<section class="getting-started">
 <div v-if="error" role="alert" class="alert">{{error}}</div>
 <article class="panel"><div class="heading"><h2>从这里完成一次调查与恢复</h2><button class="secondary" @click="refresh">刷新演示状态</button></div>
 <p>平台已经启动。下面的入口只打开保存的记录；恢复需要你审阅方案并批准。</p>
 <p class="resume-notice">演示调查和核验使用可控测试响应，运行状态、引用原文和恢复探针来自真实故障实验。真实模型调用0次，不计入模型效果评测。</p>
 <p class="muted" v-if="demo?.prepared_at">准备时间：{{new Date(demo.prepared_at).toLocaleString('zh-CN',{hour12:false})}}；报告记录当时状态，恢复前会重新检查当前状态。</p>
 <p v-if="!demo?.available" class="inline-error">{{demo?.reason??'正在读取演示'}} 准备步骤见项目根目录README与docs/DEMO.md。</p></article>
 <div class="lab-grid">
 <article class="panel"><h3>① 有依据的调查 → 人工批准 → 真实恢复</h3><p>打开worker停止调查，查看当前状态和手册原文。再生成恢复方案；批准前worker保持停止。</p><div class="guide-buttons"><button class="primary" :disabled="!demo?.success_id" @click="investigation(demo?.success_id)">打开成功调查</button><button class="secondary" :disabled="!demo?.success_id" @click="recovery">审阅并生成恢复方案</button></div><p class="muted">批准后预期：新任务完成，字符31、词5，动作记录1条。方案超过10分钟或当前状态已变，须重新生成。</p></article>
 <article class="panel"><h3>② 资料不足 → 说明缺少什么 → 保持现状</h3><p>未连接实例没有可读取的运行事实。系统不能确认原因，也不能为该调查生成恢复方案。</p><button class="secondary" :disabled="!demo?.insufficient_id" @click="investigation(demo?.insufficient_id)">打开信息不足调查</button><p class="muted">补充项只需要脱敏状态和日志，不需要密钥。</p></article>
 </div>
 <article class="panel"><h3>查看已经完成的对照与失败</h3><p>120检索题四种方式、24调查案例单／多对照，共528条结果。重复拒答输入单列共享控制，真实失败保留。</p><button class="secondary" @click="emit('navigate','evaluation')">打开对照评测</button><p class="muted">详细边界、运行步骤、复盘和简历材料见README。对外部项目仅支持只读调查和建议；当前恢复限本机故障实验。</p></article>
</section>
</template>
<style scoped>.getting-started{display:grid;gap:20px}.getting-started p{line-height:1.9;font-size:13px}.guide-buttons{display:flex;flex-wrap:wrap;gap:10px}.getting-started h3{line-height:1.8}</style>
