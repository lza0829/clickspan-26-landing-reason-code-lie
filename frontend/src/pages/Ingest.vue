<script setup>
import { ref } from 'vue'
import { api } from '../api.js'

const result = ref(null)
const err = ref('')
const busy = ref(false)
const fixture = ref('landing_hits.json')

const fixtures = [
  { id: 'landing_hits.json', label: '着陆点夹具（含 landing，间隙仅几十秒）' },
  { id: 'browse_hits.json', label: '普通浏览点夹具（无着陆事件，对照）' },
  { id: 'idle_gap.json', label: '空闲超时对照夹具（无着陆事件，40 分钟间隙）' },
  { id: 'ingest_batch.json', label: '基座收流夹具' },
]

async function run() {
  busy.value = true
  err.value = ''
  result.value = null
  try {
    result.value = await api.ingest(fixture.value)
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <!-- plant-ui -->

  <section class="panel">
    <h1>收流击点</h1>
    <p class="muted">
      读本地 SQLite 夹具灌入正式通道：着陆点强制关旧开新并落切分边，普通点走空闲超时。
    </p>
    <label>
      夹具
      <select v-model="fixture">
        <option v-for="f in fixtures" :key="f.id" :value="f.id">{{ f.label }}</option>
      </select>
    </label>
    <p>
      <button class="btn" :disabled="busy" @click="run">{{ busy ? '收流中…' : '跑一批收流' }}</button>
    </p>
    <p v-if="err" class="err">{{ err }}</p>
    <pre v-if="result" class="mono">{{ result }}</pre>
  </section>
</template>
