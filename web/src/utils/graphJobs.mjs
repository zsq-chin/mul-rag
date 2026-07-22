/**
 * Graph job utility functions and polling coordinator.
 *
 * Pure helpers -- no side-effects, no DOM, no store imports.
 * The polling coordinator uses injected fetch, callbacks, timer
 * functions, and interval so Node tests can use a deterministic
 * fake scheduler.
 */

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled', 'interrupted'])

/**
 * Return true when *status* is a terminal (non-pollable) state.
 */
export function isTerminal(status) {
  return TERMINAL_STATUSES.has(status)
}

/**
 * Clamp a numeric progress value to the 0-100 range.
 * Returns 0 for non-finite or missing values.
 */
export function normalizeProgress(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return 0
  return Math.max(0, Math.min(100, Math.round(n)))
}

/**
 * Create a polling coordinator for graph job status.
 *
 * All side-effects are injected so the coordinator is fully testable
 * with a deterministic fake scheduler in Node.
 *
 * @param {object} opts
 * @param {function(string): Promise<object>} opts.fetchJobStatus
 * @param {function(string, object)} opts.onTick         - called after each successful fetch
 * @param {function(string)} opts.onTerminal             - called when a terminal status is reached
 * @param {function(function, number): number} opts.setTimeoutFn
 * @param {function(number)} opts.clearTimeoutFn
 * @param {number} [opts.intervalMs=1500]
 * @returns {{ start(graphType, taskId), invalidate(graphType), invalidateAll() }}
 */
export function createPollingCoordinator({
  fetchJobStatus,
  onTick,
  onTerminal,
  setTimeoutFn,
  clearTimeoutFn,
  intervalMs = 1500,
}) {
  /** @type {Record<string, number>} generation-token per graphType */
  const _generation = {}
  /** @type {Record<string, number>} setTimeout handle per graphType */
  const _nextTick = {}

  function invalidate(graphType) {
    _generation[graphType] = (_generation[graphType] || 0) + 1
    if (_nextTick[graphType] != null) {
      clearTimeoutFn(_nextTick[graphType])
      delete _nextTick[graphType]
    }
  }

  function invalidateAll() {
    for (const gt of Object.keys(_generation)) {
      invalidate(gt)
    }
    for (const gt of Object.keys(_nextTick)) {
      invalidate(gt)
    }
  }

  /**
   * Start polling for a given graph type.  Replaces any existing timer.
   */
  function start(graphType, taskId) {
    invalidate(graphType)
    const gen = _generation[graphType]

    const tick = async () => {
      if (_generation[graphType] !== gen) return

      try {
        const record = await fetchJobStatus(taskId)

        if (_generation[graphType] !== gen) return
        onTick(graphType, record)

        if (isTerminal(record.status)) {
          onTerminal(graphType)
          return
        }

        _nextTick[graphType] = setTimeoutFn(tick, intervalMs)
      } catch {
        // Transient fetch failure: schedule retry if generation is still current.
        if (_generation[graphType] !== gen) return
        _nextTick[graphType] = setTimeoutFn(tick, intervalMs)
      }
    }

    _nextTick[graphType] = setTimeoutFn(tick, intervalMs)
  }

  return { start, invalidate, invalidateAll }
}
