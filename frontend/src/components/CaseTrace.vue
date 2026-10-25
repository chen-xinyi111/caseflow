<script setup lang="ts">
import {computed} from 'vue'
const props=defineProps<{row:any,project:any}>()
const emit=defineEmits<{evidence:[value:any],point:[],rebind:[]}>()
const adopted=computed(()=>props.row.trace_snapshot),point=computed(()=>props.project.state.test_points?.find((p:any)=>p.id===props.row.test_point_id))
const canRebind=computed(()=>point.value?.status==='approved'&&!point.value.blocking_reasons.length&&point.value.approval_fingerprint===point.value.dependency_fingerprint)
</script>
<template>
<details class="detail case-trace" open><summary>完整追溯 · {{row.trace_mode==='point'?adopted?.point.code+'@'+row.test_point_revision:'历史直接关联'}}</summary>
 <template v-if="row.trace_mode==='point'&&adopted"><p><strong>{{adopted.point.code}} · 采用修订{{row.test_point_revision}}</strong>：{{adopted.point.intent}}</p><p class="muted">规则草稿可通过已批准测试点使用；测试点批准、用例设计审核与实际业务执行各自记录。</p>
 <p v-for="reason in row.trace_reasons" :key="reason" class="error-text">{{reason}}</p><p v-if="row.trace_current">采用的点与依据当前有效。</p>
 <p v-for="r in adopted.rules" :key="r.id">{{r.code}}@{{r.revision}} · {{r.status==='confirmed'?'规则已确认':'采用的规则草稿'}} · {{r.text}}</p>
 <article v-for="i in adopted.public_conditions_and_context" :key="i.id"><strong>公共条件/上下文：{{i.text}}</strong><p>适用解释：{{i.applicability}}</p></article>
 <p v-for="q in adopted.confirmed_answers" :key="q.id">明确回答：{{q.question}} → {{q.answer}}</p>
 <p class="muted">依据包 {{row.basis_bundle_id}}</p><div v-for="e in adopted.sources" :key="e.id" class="source-row"><button class="text-button" @click="emit('evidence',{document_id:e.document_id,evidence_id:e.id})">{{project.state.documents.find((d:any)=>d.id===e.document_id)?.name}} · {{e.location_label}}</button><p>{{e.quote}}</p><small>冻结快照 {{e.snapshot_sha256.slice(0,16)}}…</small></div>
 <div class="actions"><button class="secondary" @click="emit('point')">查看测试点评审</button><button v-if="!row.trace_current&&!row.replaces_case_id" class="secondary" :disabled="!canRebind" @click="emit('rebind')">绑定该点当前已通过修订并保存草稿</button></div><p v-if="!row.trace_current" class="muted">请先准备新依据并复审同一测试点。只换绑定也会形成新用例修订，旧执行快照保留。</p>
 </template><p v-else class="muted">此用例保留原“用例 → 需求规则 → 原文”关系，没有补造测试点或人工评审记录。</p>
</details>
</template>
<style scoped>.case-trace{overflow-wrap:anywhere}.source-row{padding:10px 0;border-top:1px solid #e0e7e2}.source-row p{white-space:pre-wrap}.source-row small{color:#7d8c83}.case-trace button{white-space:normal}</style>
