<script setup lang="ts">
import { ref } from 'vue'
import StatsSummary from './components/StatsSummary.vue'
import RunsTable from './components/RunsTable.vue'
import RunDetail from './components/RunDetail.vue'

const selectedRunId = ref<string | null>(null)
</script>

<template>
  <header>
    <h1>Code Review Dashboard</h1>
  </header>

  <main>
    <StatsSummary />
    <RunsTable :selected-run-id="selectedRunId" @select="selectedRunId = $event" />
    <RunDetail v-if="selectedRunId" :run-id="selectedRunId" />
  </main>
</template>

<style scoped>
header {
  margin-bottom: 1.5rem;
}

h1 {
  font-size: 1.5rem;
}
</style>
