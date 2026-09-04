<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { fetchRuns, type RunSummary } from '../api'

defineProps<{ selectedRunId?: string | null }>()
const emit = defineEmits<{ select: [runId: string] }>()

const runs = ref<RunSummary[]>([])
const loading = ref(true)
const error = ref<string | null>(null)

onMounted(async () => {
  try {
    runs.value = await fetchRuns()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
})

function formatTime(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleString()
}

function formatLatency(ms: number | null): string {
  if (ms === null) return '—'
  return `${(ms / 1000).toFixed(1)}s`
}

function gateLabel(gatePassed: number | null): string {
  if (gatePassed === null) return 'not scored'
  return gatePassed === 1 ? 'pass' : 'fail'
}
</script>

<template>
  <div class="runs-table">
    <p v-if="loading">Loading runs...</p>
    <p v-else-if="error" class="error">Failed to load runs: {{ error }}</p>
    <p v-else-if="runs.length === 0">No runs yet.</p>

    <table v-else>
      <thead>
        <tr>
          <th>Started</th>
          <th>Label</th>
          <th>Repo / PR</th>
          <th>Agents</th>
          <th>Latency</th>
          <th>Shadow score</th>
          <th>Gate</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="run in runs"
          :key="run.run_id"
          :class="{ selected: run.run_id === selectedRunId }"
          @click="emit('select', run.run_id)"
        >
          <td>{{ formatTime(run.started_at) }}</td>
          <td>{{ run.label }}</td>
          <td>{{ run.repo ? `${run.repo} #${run.pr_number}` : '—' }}</td>
          <td>{{ run.agents.map((a) => a.agent).join(', ') || '—' }}</td>
          <td>{{ formatLatency(run.total_latency_ms) }}</td>
          <td>{{ run.shadow_score !== null ? run.shadow_score.toFixed(2) : '—' }}</td>
          <td :class="`gate-${gateLabel(run.gate_passed)}`">{{ gateLabel(run.gate_passed) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
table {
  width: 100%;
  border-collapse: collapse;
}

th,
td {
  text-align: left;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid #e0e0e0;
}

tbody tr {
  cursor: pointer;
}

tbody tr:hover {
  background: #f5f5f5;
}

tbody tr.selected {
  background: #e8f0fe;
}

.error {
  color: #b00020;
}

.gate-pass {
  color: #1a7f37;
}

.gate-fail {
  color: #b00020;
}
</style>
