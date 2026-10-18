<script setup lang="ts">
import type {NextStep} from '../caseflowWorkflow'
defineProps<{next:NextStep}>()
defineEmits<{proceed:[]}>()
const stages=['提供需求','确认规则','审核清单','导出交付']
</script>
<template>
<section class="workflow-guide" aria-label="当前进度与下一步">
 <ol class="workflow-stages"><li v-for="(stage,i) in stages" :key="stage" :class="{current:i===next.step,passed:i<next.step}" :aria-current="i===next.step?'step':undefined"><span>{{i<next.step?'✓':i+1}}</span>{{stage}}</li></ol>
 <div class="workflow-next"><div><small>现在做什么</small><h2>{{next.title}}</h2><p>{{next.description}}</p></div><button v-if="next.action" class="primary" @click="$emit('proceed')">{{next.action}} →</button></div>
</section>
</template>
<style scoped>
.workflow-guide{border:1px solid #cbded2;background:#f4faf6;border-radius:12px;padding:20px 24px;margin-bottom:24px}.workflow-stages{list-style:none;padding:0;margin:0 0 20px;display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.workflow-stages li{display:flex;align-items:center;gap:8px;color:#84958c;font-size:12px}.workflow-stages span{display:grid;place-items:center;border-radius:50%;width:24px;height:24px;border:1px solid #d7e3db;flex-shrink:0}.workflow-stages .current{color:#245d46;font-weight:650}.current span{background:#245d46;color:white;border-color:#245d46}.workflow-stages .passed{color:#5c806c}.workflow-next{display:flex;justify-content:space-between;align-items:center;gap:20px}.workflow-next small{font-size:11px;color:#60816c}.workflow-next h2{margin:5px 0}.workflow-next p{margin:5px 0 0;max-width:640px;font-size:13px;color:#5b7263;line-height:1.7}@media(max-width:700px){.workflow-guide{padding:18px}.workflow-stages{gap:7px}.workflow-stages li{font-size:11px;flex-direction:column;align-items:flex-start}.workflow-next{align-items:flex-start;flex-direction:column}.workflow-next button{width:100%}}
</style>
