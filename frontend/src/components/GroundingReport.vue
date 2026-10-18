<script setup lang="ts">
import {groundingLabel} from '../caseflowGrounding'
defineProps<{row:any,changed?:boolean,requirements:any[]}>()
const field:Record<string,string>={preconditions:'前置条件',data:'输入数据',steps:'操作步骤',expected:'预期结果'}
const verdict:Record<string,string>={supported:'模型判断有依据',test_data:'构造的测试条件或数据',test_action:'通用检查动作',needs_clarification:'需要澄清',unsupported:'发现无依据要求'}
</script>
<template>
<details class="detail grounding-report" :open="!!row.grounding">
 <summary>依据检查 · {{changed?'内容已修改，原检查待更新':groundingLabel(row)}}</summary>
 <p class="muted">自动复核可能漏判；“有依据”是模型判断，审核决定仍由你确认。</p>
 <p v-if="!row.grounding" class="muted">尚未检查。确认下方使用的资料后，可检查已保存内容。</p>
 <p v-if="row.grounding&&(changed||!row.grounding.current)" class="message">下面是旧内容的检查记录。请先保存，再重新检查；也可逐项人工核对后明确确认。</p>
 <article v-for="item in row.grounding?.fields??[]" :key="item.field" class="field-diff">
  <strong>{{field[item.field]}} · {{verdict[item.status]}}</strong><p>{{item.reason}}</p>
  <small class="muted" v-if="item.requirement_ids.length">关联规则：{{item.requirement_ids.map((id:string)=>requirements.find(r=>r.id===id)?.code??id).join('、')}}</small>
 </article>
 <details v-if="row.calculation"><summary>查看数值计算</summary><p>按已确认规则“{{row.calculation.rule_name}}”，输入 {{row.calculation.input}}{{row.calculation.unit}}；输入数据与预期结果由程序生成。</p><p class="muted">计算前输入：{{row.calculation.original_data}}</p><p class="muted">计算前预期：{{row.calculation.original_expected}}</p></details>
</details>
</template>
