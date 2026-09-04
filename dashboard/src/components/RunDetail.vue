<script setup lang="ts">
import { ref, watch } from 'vue'
import { fetchRun, type RunDetail as RunDetailData } from '../api'

const props = defineProps<{ runId: string }>()

const detail = ref<RunDetailData | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

async function load(runId: string) {
  loading.value = true
  error.value = null
  try {
    detail.value = await fetchRun(runId)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

watch(() => props.runId, load, { immediate: true })

function formatLatency(ms: number | null): string {
  if (ms === null) return '—'
  return `${(ms / 1000).toFixed(1)}s`
}
</script>

<template>
  <div class="run-detail">
    <p v-if="loading">Loading run {{ runId }}...</p>
    <p v-else-if="error" class="error">Failed to load run: {{ error }}</p>

    <template v-else-if="detail">
      <h2>{{ detail.label }}</h2>
      <p class="meta">
        run_id: {{ detail.run_id }} &middot; status: {{ detail.status }} &middot;
        total latency: {{ formatLatency(detail.total_latency_ms) }}
      </p>

      <section>
        <h3>Agents</h3>
        <ul>
          <li v-for="agent in detail.agents" :key="agent.agent">
            {{ agent.agent }} — {{ formatLatency(agent.latency_ms) }}
            <span v-if="agent.output_chars !== undefined">({{ agent.output_chars }} chars)</span>
          </li>
        </ul>
      </section>

      <section>
        <h3>Gate decision</h3>
        <p v-if="!detail.gate_decision">Not scored (shadow scoring is sampled at 10% of runs).</p>
        <div v-else>
          <p :class="detail.gate_decision.passed ? 'gate-pass' : 'gate-fail'">
            {{ detail.gate_decision.passed ? 'PASS' : 'FAIL' }} — score
            {{ detail.gate_decision.score.toFixed(2) }}
            (threshold {{ detail.gate_decision.threshold.toFixed(2) }})
          </p>
          <p>{{ detail.gate_decision.notes }}</p>
          <ul v-if="detail.gate_decision.breakdown">
            <li v-for="(value, key) in detail.gate_decision.breakdown" :key="key">
              {{ key }}: {{ value }}
            </li>
          </ul>
        </div>
      </section>

      <section>
        <h3>Cost</h3>
        <p v-if="!detail.cost_estimate">No cost data recorded.</p>
        <p v-else>
          {{ detail.cost_estimate.prompt_tokens }} prompt +
          {{ detail.cost_estimate.completion_tokens }} completion tokens — real:
          ${{ detail.cost_estimate.real_cost_usd.toFixed(4) }}, reference
          ({{ detail.cost_estimate.reference_model }}):
          ${{ detail.cost_estimate.reference_cost_usd?.toFixed(4) ?? 'n/a' }}
        </p>
      </section>

      <section>
        <h3>Report</h3>
        <pre class="report">{{ detail.output }}</pre>
      </section>
    </template>
  </div>
</template>

<style scoped>
.meta {
  color: #666;
  font-size: 0.9rem;
}

section {
  margin-top: 1.5rem;
}

.report {
  white-space: pre-wrap;
  background: #f7f7f7;
  padding: 1rem;
  border-radius: 4px;
  max-height: 500px;
  overflow-y: auto;
}

.error {
  color: #b00020;
}

.gate-pass {
  color: #1a7f37;
  font-weight: bold;
}

.gate-fail {
  color: #b00020;
  font-weight: bold;
}
</style>
