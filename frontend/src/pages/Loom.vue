<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const overview = ref(null)
const sessions = ref([])
const details = ref({})
const err = ref('')

async function load() {
  err.value = ''
  try {
    overview.value = await api.overview()
    sessions.value = await api.sessions()
    const map = {}
    for (const s of sessions.value) {
      map[s.id] = await api.session(s.id)
    }
    details.value = map
  } catch (e) {
    err.value = String(e.message || e)
  }
}

onMounted(load)
</script>

<template>
  <!-- plant-ui -->

  <div>
    <div class="stats" v-if="overview">
      <div class="stat"><b>{{ overview.visitors }}</b><span>访客</span></div>
      <div class="stat"><b>{{ overview.sessions }}</b><span>会话</span></div>
      <div class="stat"><b>{{ overview.hits_primary }}</b><span>正式击点</span></div>
      <div class="stat"><b>{{ overview.quarantine }}</b><span>隔离</span></div>
      <div class="stat"><b>{{ overview.idle_timeout_sec }}s</b><span>空闲超时</span></div>
      <div class="stat"><b>{{ overview.landing_splits }}</b><span>着陆切分边</span></div>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <section class="panel">
      <h1>会话织机</h1>
      <p class="muted">
        着陆点（命中规则集合）无视间隙强制关旧开新，切分边带规则版本；普通浏览点走空闲超时切窗。
      </p>
      <div class="lane-list">
        <article v-for="s in sessions" :key="s.id" class="lane">
          <div class="lane-head">
            <div>
              <strong>{{ s.id }}</strong>
              <span class="muted"> · {{ s.visitor_id }} · {{ s.status }}</span>
              <span v-if="s.landing_rule_version" class="ok">
                · 着陆规则 v{{ s.landing_rule_version }}
              </span>
            </div>
            <span class="mono">{{ s.hit_count }} 点 · {{ s.started_at }} → {{ s.ended_at || '…' }}</span>
          </div>
          <p v-if="details[s.id]?.landing_split_in" class="ok">
            由着陆点 {{ details[s.id].landing_split_in.trigger_hit_id }}
            （{{ details[s.id].landing_split_in.event_name }} →
            {{ details[s.id].landing_split_in.landing_url }}）强制切开，原因
            {{ details[s.id].landing_split_in.reason }}，规则版本
            v{{ details[s.id].landing_split_in.rule_version }}
          </p>
          <div class="beads" v-if="details[s.id]">
            <template v-for="(h, i) in details[s.id].hits" :key="h.id">
              <span v-if="i" class="connector" aria-hidden="true"></span>
              <span class="hit-bead" :class="{ landing: h.id === details[s.id].landing_split_in?.trigger_hit_id }">
                <strong>{{ h.event_name }}</strong>
                <span>{{ h.page_path }}</span>
              </span>
            </template>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>
