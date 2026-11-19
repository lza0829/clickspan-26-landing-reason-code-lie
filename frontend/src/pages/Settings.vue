<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const sec = ref(1800)
const msg = ref('')
const err = ref('')

const rules = ref([])
const namesText = ref('')
const note = ref('')
const ruleMsg = ref('')
const ruleErr = ref('')

onMounted(async () => {
  try {
    const s = await api.settings()
    sec.value = s.idle_timeout_sec
    await loadRules()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

async function loadRules() {
  rules.value = await api.landingRules()
  const active = rules.value.find((r) => r.active)
  namesText.value = active ? active.event_names.join(', ') : ''
  note.value = active ? active.note : ''
}

async function save() {
  err.value = ''
  msg.value = ''
  try {
    const res = await fetch('/api/settings/idle-timeout', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idle_timeout_sec: Number(sec.value) }),
    })
    if (!res.ok) throw new Error(await res.text())
    const data = await res.json()
    sec.value = data.idle_timeout_sec
    msg.value = '已保存：非着陆事件按此空闲超时切窗'
  } catch (e) {
    err.value = String(e.message || e)
  }
}

async function saveRules() {
  ruleErr.value = ''
  ruleMsg.value = ''
  const names = namesText.value
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
  try {
    const rule = await api.saveLandingRules(names, note.value)
    ruleMsg.value = `已落新版本快照 v${rule.version}（旧版本保留为非活跃）`
    await loadRules()
  } catch (e) {
    ruleErr.value = String(e.message || e)
  }
}
</script>

<template>
  <section class="panel">
    <h1>空闲切窗</h1>
    <p class="muted">非着陆事件：相邻击点间隙超过该秒数才另开会话。</p>
    <label>
      秒数
      <input v-model.number="sec" type="number" min="60" step="60" class="mono" />
    </label>
    <p>
      <button class="btn" @click="save">保存</button>
    </p>
    <p v-if="msg" class="ok">{{ msg }}</p>
    <p v-if="err" class="err">{{ err }}</p>

    <hr />

    <h1>着陆事件集合</h1>
    <p class="muted">
      命中集合的击点收流时无视间隙，先关旧 open 再开新会话；每次保存都生成不可变版本快照，
      切分边记录所用版本。事件名只存数据库，代码不写死。
    </p>
    <label>
      事件名（逗号分隔）
      <input v-model="namesText" type="text" class="mono" placeholder="landing, landing_page" />
    </label>
    <label>
      备注
      <input v-model="note" type="text" />
    </label>
    <p>
      <button class="btn" @click="saveRules">保存为新版本</button>
    </p>
    <p v-if="ruleMsg" class="ok">{{ ruleMsg }}</p>
    <p v-if="ruleErr" class="err">{{ ruleErr }}</p>

    <table class="data">
      <thead>
        <tr><th>版本</th><th>状态</th><th>事件名集合</th><th>备注</th><th>创建时间</th></tr>
      </thead>
      <tbody>
        <tr v-for="r in rules" :key="r.version">
          <td class="mono">v{{ r.version }}</td>
          <td>
            <span :class="r.active ? 'ok' : 'muted'">{{ r.active ? '生效中' : '已归档' }}</span>
          </td>
          <td class="mono">{{ r.event_names.join(', ') }}</td>
          <td>{{ r.note }}</td>
          <td class="mono">{{ r.created_at }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
