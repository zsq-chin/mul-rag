/**
 * Graph job progress store.
 *
 * Persists active task IDs by graph type in localStorage under
 * the key "graph-active-jobs".  On store initialization each saved
 * task is fetched; only terminal statuses cause removal.  Polling
 * uses the production createPollingCoordinator from graphJobs.mjs
 * with generation-token race safety.
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { graphJobApi } from '@/apis/admin_api'
import { isTerminal, normalizeProgress, createPollingCoordinator } from '@/utils/graphJobs'

const STORAGE_KEY = 'graph-active-jobs'
const POLL_INTERVAL_MS = 1500

/**
 * Read persisted active jobs from localStorage.
 * Returns a plain object: { ground: taskId | null, drill: taskId | null }
 */
function _readStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function _writeStorage(data) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
  } catch {
    // storage full or unavailable -- ignore
  }
}

export const useGraphJobStore = defineStore('graphJobs', () => {
  // --- reactive state ---

  /** Current job record for each graph type (null when idle). */
  const jobs = ref({})

  // --- polling coordinator ---

  const coordinator = createPollingCoordinator({
    fetchJobStatus: graphJobApi.getJob,
    onTick(graphType, record) {
      jobs.value[graphType] = { ...record, progress: normalizeProgress(record.progress) }
    },
    onTerminal(graphType) {
      _removeActive(graphType)
    },
    setTimeoutFn: (fn, ms) => setTimeout(fn, ms),
    clearTimeoutFn: (id) => clearTimeout(id),
    intervalMs: POLL_INTERVAL_MS,
  })

  // --- private helpers ---

  function _setActive(graphType, taskId) {
    const saved = _readStorage()
    saved[graphType] = taskId
    _writeStorage(saved)
  }

  function _removeActive(graphType) {
    const saved = _readStorage()
    delete saved[graphType]
    _writeStorage(saved)
  }

  // --- public actions ---

  /**
   * Submit a new job for the given graph type.
   * Returns the created job record.
   */
  async function submitJob(graphType) {
    const record = await graphJobApi.submitJob(graphType)
    const normalized = { ...record, progress: normalizeProgress(record.progress) }
    jobs.value[graphType] = normalized
    _setActive(graphType, normalized.id)
    coordinator.start(graphType, normalized.id)
    return normalized
  }

  /**
   * Request cancellation of an active job.
   * Normalizes the response.  If terminal, invalidates polling and
   * removes localStorage immediately.  If non-terminal (e.g. cancelling),
   * keeps the active ID persisted and lets polling continue.
   */
  async function cancelJob(graphType) {
    const job = jobs.value[graphType]
    if (!job) return null
    const record = await graphJobApi.cancelJob(job.id)
    const normalized = { ...record, progress: normalizeProgress(record.progress) }
    jobs.value[graphType] = normalized

    if (isTerminal(normalized.status)) {
      // Terminal: stop polling and clear persistence now.
      coordinator.invalidate(graphType)
      _removeActive(graphType)
    }
    // Non-terminal (e.g. cancelling): keep persistence and let
    // the running poll cycle pick up the terminal transition.
    return normalized
  }

  /**
   * Retry a terminal (failed/cancelled/interrupted) job.
   */
  async function retryJob(graphType) {
    const job = jobs.value[graphType]
    if (!job) return null
    const record = await graphJobApi.retryJob(job.id)
    const normalized = { ...record, progress: normalizeProgress(record.progress) }
    jobs.value[graphType] = normalized
    _setActive(graphType, normalized.id)
    coordinator.start(graphType, normalized.id)
    return normalized
  }

  /**
   * Recover persisted active jobs on store initialization / page refresh.
   * Fetches each saved task and resumes polling if still active.
   * On fetch error the stored ID is preserved -- the job may become
   * reachable later or the user can retry manually.
   */
  async function recoverJobs() {
    const saved = _readStorage()
    for (const [graphType, taskId] of Object.entries(saved)) {
      if (!taskId) continue
      try {
        const record = await graphJobApi.getJob(taskId)
        const normalized = { ...record, progress: normalizeProgress(record.progress) }
        if (isTerminal(normalized.status)) {
          // Show terminal result, then clear persistence.
          jobs.value[graphType] = normalized
          _removeActive(graphType)
        } else {
          jobs.value[graphType] = normalized
          coordinator.start(graphType, taskId)
        }
      } catch {
        // Transient error -- preserve the stored ID and start
        // polling so the page automatically recovers when the
        // endpoint becomes reachable.  Do NOT call _removeActive.
        coordinator.start(graphType, taskId)
      }
    }
  }

  /**
   * Get the current job for a graph type (reactive).
   */
  function getJob(graphType) {
    return jobs.value[graphType] || null
  }

  /**
   * Clean up all timers.  Call from onBeforeUnmount or teardown.
   */
  function dispose() {
    coordinator.invalidateAll()
  }

  return {
    jobs,
    submitJob,
    cancelJob,
    retryJob,
    recoverJobs,
    getJob,
    dispose,
  }
})
