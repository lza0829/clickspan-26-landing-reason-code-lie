<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const rows = ref([])
const splits = ref([])
const verify = ref(null)
const reasonVerify = ref(null)
const err = ref('')

onMounted(async () => {
  try {
    rows.value = await api.proofs()
    splits.value = await api.landingSplits()
    verify.value = await api.landingVerify()
    reasonVerify.value = await api.landingReasonVerify()
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>

<template>
  <!-- plant-ui -->

  <section class="panel">
    <h1>会话核对</h1>
    <p class="muted">会话击点集合与起止须与织机汇总可核。</p>
    <p v-if="err" class="err">{{ err }}</p>

    <h2>着陆切开核对（只读）</h2>
    <p v-if="verify" :class="verify.ok ? 'ok' : 'err'">
      {{ verify.ok ? `核对通过：${verify.split_count} 条切分边全部一致` : '核对发现不一致' }}
    </p>
    <table v-if="splits.length" class="data">
      <thead>
        <tr>
          <th>规则版本</th><th>访客</th><th>触发点</th><th>事件名</th>
          <th>原因</th><th>落地 URL</th>
          <th>关闭旧会话</th><th>新开会话</th><th>时间</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="s in splits" :key="s.id">
          <td class="mono">v{{ s.rule_version }}</td>
          <td>{{ s.visitor_id }}</td>
          <td class="mono">{{ s.trigger_hit_id }}</td>
          <td class="mono">{{ s.event_name }}</td>
          <td class="mono">{{ s.reason }}</td>
          <td class="mono">{{ s.landing_url }}</td>
          <td class="mono">{{ s.closed_session_id }}</td>
          <td class="mono">{{ s.new_session_id }}</td>
          <td class="mono">{{ s.event_ts }}</td>
        </tr>
      </tbody>
    </table>
    <ul v-if="verify && !verify.ok" class="err">
      <li v-for="(v, i) in verify.violations" :key="i">{{ v.问题 }}（边 #{{ v.id }}）</li>
    </ul>

    <h2>落地原因核证（只读）</h2>
    <p class="muted">
      逐条切边核对触发键、落地 URL 与原因码：落地切开必须是落地类码，超时切开不得误标落地。
    </p>
    <p v-if="reasonVerify" :class="reasonVerify.ok ? 'ok' : 'err'">
      {{ reasonVerify.ok
        ? `原因核对通过：${reasonVerify.edge_count} 条切边 reason / 触发键 / URL 全部一致`
        : '原因核对发现不一致' }}
    </p>
    <table v-if="reasonVerify && reasonVerify.edges.length" class="data">
      <thead>
        <tr><th>触发键</th><th>落地 URL</th><th>原因</th><th>结论</th></tr>
      </thead>
      <tbody>
        <tr v-for="e in reasonVerify.edges" :key="e.split_id">
          <td class="mono">{{ e.trigger_hit_id }}</td>
          <td class="mono">{{ e.landing_url }}</td>
          <td class="mono">{{ e.reason }}（{{ e.reason_label }}）</td>
          <td :class="e.conclusion === 'ok' ? 'ok' : 'err'">
            {{ e.conclusion === 'ok' ? '一致' : e.problems.join('；') }}
          </td>
        </tr>
      </tbody>
    </table>

    <h2>会话证明</h2>
    <table class="data">
      <thead>
        <tr><th>会话</th><th>访客</th><th>点数</th><th>起止</th><th>击点集</th></tr>
      </thead>
      <tbody>
        <tr v-for="r in rows" :key="r.id">
          <td class="mono">{{ r.session_id }}</td>
          <td>{{ r.visitor_id }}</td>
          <td>{{ r.hit_count }}</td>
          <td class="mono">{{ r.started_at }} → {{ r.ended_at }}</td>
          <td class="mono">{{ r.hit_ids_json }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
