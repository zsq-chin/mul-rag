import test from 'node:test'
import assert from 'node:assert/strict'
import {
  isTerminal,
  normalizeProgress,
  createPollingCoordinator,
} from '../src/utils/graphJobs.mjs'

// ---------------------------------------------------------------------------
// isTerminal / normalizeProgress (unchanged)
// ---------------------------------------------------------------------------

test('only terminal states stop polling', () => {
  assert.equal(isTerminal('completed'), true)
  assert.equal(isTerminal('failed'), true)
  assert.equal(isTerminal('cancelled'), true)
  assert.equal(isTerminal('interrupted'), true)
  assert.equal(isTerminal('building'), false)
  assert.equal(isTerminal('queued'), false)
  assert.equal(isTerminal('copying'), false)
  assert.equal(isTerminal('converting'), false)
  assert.equal(isTerminal('importing'), false)
  assert.equal(isTerminal('indexing'), false)
  assert.equal(isTerminal('cancelling'), false)
})

test('unknown statuses are not terminal', () => {
  assert.equal(isTerminal('unknown'), false)
  assert.equal(isTerminal(''), false)
  assert.equal(isTerminal(undefined), false)
  assert.equal(isTerminal(null), false)
})

test('progress is clamped to 0-100', () => {
  assert.equal(normalizeProgress(120), 100)
  assert.equal(normalizeProgress(-2), 0)
  assert.equal(normalizeProgress(50), 50)
  assert.equal(normalizeProgress(0), 0)
  assert.equal(normalizeProgress(100), 100)
  assert.equal(normalizeProgress(NaN), 0)
  assert.equal(normalizeProgress(null), 0)
  assert.equal(normalizeProgress(undefined), 0)
})

test('progress handles string numbers', () => {
  assert.equal(normalizeProgress('75'), 75)
  assert.equal(normalizeProgress('0'), 0)
  assert.equal(normalizeProgress('100'), 100)
  assert.equal(normalizeProgress('abc'), 0)
  assert.equal(normalizeProgress(''), 0)
})

test('progress rounds floats', () => {
  assert.equal(normalizeProgress(50.4), 50)
  assert.equal(normalizeProgress(50.6), 51)
  assert.equal(normalizeProgress(0.1), 0)
  assert.equal(normalizeProgress(99.9), 100)
})

test('progress handles Infinity and -Infinity', () => {
  assert.equal(normalizeProgress(Infinity), 0)
  assert.equal(normalizeProgress(-Infinity), 0)
})

// ---------------------------------------------------------------------------
// Fake scheduler for deterministic polling tests
// ---------------------------------------------------------------------------

/**
 * Create a deterministic fake scheduler.
 * Pending callbacks are stored by handle and drained one at a time
 * with `flush()` or all at once with `flushAll()`.
 */
function createFakeScheduler() {
  let nextHandle = 1
  const pending = new Map() // handle -> { fn, ms }

  function setTimeoutFn(fn, ms) {
    const handle = nextHandle++
    pending.set(handle, { fn, ms })
    return handle
  }

  function clearTimeoutFn(handle) {
    pending.delete(handle)
  }

  /** Flush the next pending timer. Returns its handle, or null if none. */
  async function flush() {
    const [handle, entry] = pending.entries().next().value
    if (!entry) return null
    pending.delete(handle)
    await entry.fn()
    return handle
  }

  /** Flush all pending timers (depth-first). */
  async function flushAll() {
    while (pending.size > 0) {
      await flush()
    }
  }

  return { setTimeoutFn, clearTimeoutFn, flush, flushAll, pending }
}

// ---------------------------------------------------------------------------
// Polling coordinator: terminal first-fetch stops with no next timer
// ---------------------------------------------------------------------------

test('terminal first fetch stops and calls onTerminal with no next timer', async () => {
  const scheduler = createFakeScheduler()
  const fetched = []
  const terminals = []
  const ticks = []

  const coordinator = createPollingCoordinator({
    fetchJobStatus: async (taskId) => {
      fetched.push(taskId)
      return { id: taskId, status: 'completed', progress: 100 }
    },
    onTick(graphType, record) {
      ticks.push({ graphType, status: record.status })
    },
    onTerminal(graphType) {
      terminals.push(graphType)
    },
    setTimeoutFn: scheduler.setTimeoutFn,
    clearTimeoutFn: scheduler.clearTimeoutFn,
    intervalMs: 1000,
  })

  coordinator.start('ground', 'a'.repeat(32))

  // One tick is scheduled (the first delayed fire).
  assert.equal(scheduler.pending.size, 1)

  // Flush it -- fetch returns terminal.
  await scheduler.flush()

  // onTick was called with the terminal record.
  assert.equal(ticks.length, 1)
  assert.equal(ticks[0].status, 'completed')

  // onTerminal was called.
  assert.deepEqual(terminals, ['ground'])

  // No further timer was scheduled -- polling stopped.
  assert.equal(scheduler.pending.size, 0)

  // fetch was called exactly once.
  assert.equal(fetched.length, 1)
})

// ---------------------------------------------------------------------------
// Polling coordinator: transient fetch failure schedules retry, no onTerminal
// ---------------------------------------------------------------------------

test('transient fetch failure schedules retry and does not call onTerminal', async () => {
  const scheduler = createFakeScheduler()
  let fetchCount = 0
  const terminals = []

  const coordinator = createPollingCoordinator({
    fetchJobStatus: async () => {
      fetchCount++
      if (fetchCount <= 2) throw new Error('network down')
      return { id: 'x', status: 'building', progress: 50 }
    },
    onTick() {},
    onTerminal(graphType) {
      terminals.push(graphType)
    },
    setTimeoutFn: scheduler.setTimeoutFn,
    clearTimeoutFn: scheduler.clearTimeoutFn,
    intervalMs: 500,
  })

  coordinator.start('drill', 'b'.repeat(32))

  // First tick: fetch throws -> retry scheduled (no onTerminal).
  await scheduler.flush()
  assert.equal(fetchCount, 1)
  assert.equal(terminals.length, 0)
  assert.equal(scheduler.pending.size, 1)

  // Second tick: fetch throws again -> retry scheduled.
  await scheduler.flush()
  assert.equal(fetchCount, 2)
  assert.equal(terminals.length, 0)
  assert.equal(scheduler.pending.size, 1)

  // Third tick: fetch succeeds with non-terminal -> next tick scheduled.
  await scheduler.flush()
  assert.equal(fetchCount, 3)
  assert.equal(terminals.length, 0)
  assert.equal(scheduler.pending.size, 1)
})

// ---------------------------------------------------------------------------
// Polling coordinator: replacement/dispose invalidates in-flight result
// ---------------------------------------------------------------------------

test('replacement start invalidates the old generation -- stale result ignored', async () => {
  const scheduler = createFakeScheduler()
  let fetchCount = 0
  const terminals = []
  const tickStatuses = []

  const coordinator = createPollingCoordinator({
    fetchJobStatus: async () => {
      fetchCount++
      // First call returns a slow (non-terminal) result.
      // Second call (new generation) returns terminal.
      if (fetchCount === 1) {
        return { id: 'x', status: 'building', progress: 30 }
      }
      return { id: 'x', status: 'completed', progress: 100 }
    },
    onTick(_gt, record) {
      tickStatuses.push(record.status)
    },
    onTerminal(graphType) {
      terminals.push(graphType)
    },
    setTimeoutFn: scheduler.setTimeoutFn,
    clearTimeoutFn: scheduler.clearTimeoutFn,
    intervalMs: 100,
  })

  coordinator.start('ground', 'c'.repeat(32))

  // First tick: fetch returns building -> schedules next tick.
  await scheduler.flush()
  assert.deepEqual(tickStatuses, ['building'])
  assert.equal(scheduler.pending.size, 1)

  // Now replace with a new start -- old pending tick is invalidated.
  coordinator.start('ground', 'd'.repeat(32))
  // Old tick was cleared; new tick is pending.
  assert.equal(scheduler.pending.size, 1)

  // Flush the new tick: fetch returns terminal.
  await scheduler.flush()
  assert.deepEqual(tickStatuses, ['building', 'completed'])
  assert.deepEqual(terminals, ['ground'])
  assert.equal(scheduler.pending.size, 0)
})

test('invalidateAll prevents any pending tick from firing', async () => {
  const scheduler = createFakeScheduler()
  const fetched = []

  const coordinator = createPollingCoordinator({
    fetchJobStatus: async (taskId) => {
      fetched.push(taskId)
      return { id: taskId, status: 'building', progress: 10 }
    },
    onTick() {},
    onTerminal() {},
    setTimeoutFn: scheduler.setTimeoutFn,
    clearTimeoutFn: scheduler.clearTimeoutFn,
    intervalMs: 100,
  })

  coordinator.start('ground', 'e'.repeat(32))
  coordinator.start('drill', 'f'.repeat(32))
  assert.equal(scheduler.pending.size, 2)

  coordinator.invalidateAll()
  assert.equal(scheduler.pending.size, 0)

  // Draining does nothing -- no fetches happen.
  await scheduler.flushAll()
  assert.equal(fetched.length, 0)
})

// ---------------------------------------------------------------------------
// Polling coordinator: non-overlapping -- at most one tick pending per graphType
// ---------------------------------------------------------------------------

test('polling is non-overlapping: only one timer per graphType at a time', async () => {
  const scheduler = createFakeScheduler()
  let fetchCount = 0

  const coordinator = createPollingCoordinator({
    fetchJobStatus: async () => {
      fetchCount++
      return { id: 'x', status: 'building', progress: fetchCount * 10 }
    },
    onTick() {},
    onTerminal() {},
    setTimeoutFn: scheduler.setTimeoutFn,
    clearTimeoutFn: scheduler.clearTimeoutFn,
    intervalMs: 200,
  })

  coordinator.start('ground', 'aa'.repeat(16))

  // Exactly one timer is pending.
  assert.equal(scheduler.pending.size, 1)

  // Flush tick 1 -> fetch returns building -> schedules tick 2.
  await scheduler.flush()
  assert.equal(fetchCount, 1)
  assert.equal(scheduler.pending.size, 1) // still exactly one

  // Flush tick 2 -> same pattern.
  await scheduler.flush()
  assert.equal(fetchCount, 2)
  assert.equal(scheduler.pending.size, 1)
})

// ---------------------------------------------------------------------------
// Polling coordinator: cancel terminal response removes persistence
// (tested via a store-like cancel wrapper using the coordinator)
// ---------------------------------------------------------------------------

test('cancel returning terminal invalidates polling and removes persistence', async () => {
  const scheduler = createFakeScheduler()
  const removedFromStorage = []
  const stored = []

  // Simulate a tiny store with cancelJob behavior.
  const jobs = {}
  let activeId = 'a'.repeat(32)

  function _removeActive(graphType) {
    removedFromStorage.push(graphType)
  }
  function _setActive(graphType, taskId) {
    stored.push({ graphType, taskId })
  }

  const coordinator = createPollingCoordinator({
    fetchJobStatus: async () => ({ id: activeId, status: 'building', progress: 10 }),
    onTick(_gt, record) {
      jobs[_gt] = record
    },
    onTerminal(graphType) {
      _removeActive(graphType)
    },
    setTimeoutFn: scheduler.setTimeoutFn,
    clearTimeoutFn: scheduler.clearTimeoutFn,
    intervalMs: 100,
  })

  // Start polling.
  coordinator.start('ground', activeId)
  assert.equal(scheduler.pending.size, 1)

  // Simulate cancelJob returning a terminal status.
  const cancelRecord = { id: activeId, status: 'cancelled', progress: 25 }
  jobs['ground'] = cancelRecord

  // cancelJob logic: if terminal, invalidate + removeActive.
  if (isTerminal(cancelRecord.status)) {
    coordinator.invalidate('ground')
    _removeActive('ground')
  }

  // Polling was invalidated.
  assert.equal(scheduler.pending.size, 0)
  // Persistence was removed.
  assert.deepEqual(removedFromStorage, ['ground'])
})

test('cancel returning non-terminal keeps persistence and polling alive', async () => {
  const scheduler = createFakeScheduler()
  const removedFromStorage = []
  const stored = []

  let activeId = 'b'.repeat(32)
  const jobs = {}

  function _removeActive(graphType) {
    removedFromStorage.push(graphType)
  }
  function _setActive(graphType, taskId) {
    stored.push({ graphType, taskId })
  }

  const coordinator = createPollingCoordinator({
    fetchJobStatus: async () => ({ id: activeId, status: 'building', progress: 10 }),
    onTick(_gt, record) {
      jobs[_gt] = record
    },
    onTerminal(graphType) {
      _removeActive(graphType)
    },
    setTimeoutFn: scheduler.setTimeoutFn,
    clearTimeoutFn: scheduler.clearTimeoutFn,
    intervalMs: 100,
  })

  coordinator.start('drill', activeId)
  assert.equal(scheduler.pending.size, 1)

  // Simulate cancelJob returning a non-terminal status (cancelling).
  const cancelRecord = { id: activeId, status: 'cancelling', progress: 50 }
  jobs['drill'] = cancelRecord

  // cancelJob logic: non-terminal -> do NOT invalidate, do NOT removeActive.
  if (isTerminal(cancelRecord.status)) {
    coordinator.invalidate('drill')
    _removeActive('drill')
  }

  // Polling is still alive.
  assert.equal(scheduler.pending.size, 1)
  // Persistence was NOT removed.
  assert.deepEqual(removedFromStorage, [])

  // Flush the tick -- poll continues normally.
  await scheduler.flush()
  assert.equal(scheduler.pending.size, 1) // next tick scheduled
  assert.deepEqual(removedFromStorage, []) // still not terminal from poll
})

// ---------------------------------------------------------------------------
// Polling coordinator: deferred-Promise in-flight invalidation
// ---------------------------------------------------------------------------

test('in-flight fetch result discarded after invalidateAll', async () => {
  const scheduler = createFakeScheduler()
  let resolveFetch
  const onTickCalls = []
  const onTerminalCalls = []

  const coordinator = createPollingCoordinator({
    fetchJobStatus: () => new Promise((resolve) => { resolveFetch = resolve }),
    onTick(gt, record) { onTickCalls.push({ gt, status: record.status }) },
    onTerminal(gt) { onTerminalCalls.push(gt) },
    setTimeoutFn: scheduler.setTimeoutFn,
    clearTimeoutFn: scheduler.clearTimeoutFn,
    intervalMs: 100,
  })

  coordinator.start('ground', 'a'.repeat(32))
  assert.equal(scheduler.pending.size, 1)

  // Fire the tick WITHOUT awaiting -- the fetch blocks on a deferred promise.
  const [handle, entry] = scheduler.pending.entries().next().value
  scheduler.pending.delete(handle)
  const staleTickPromise = entry.fn()

  // While the fetch is still in-flight, invalidate everything.
  coordinator.invalidateAll()

  // Now resolve the stale fetch with a terminal result.
  resolveFetch({ id: 'a'.repeat(32), status: 'completed', progress: 100 })

  // Await the tick -- it must silently discard the stale result.
  await staleTickPromise

  // The stale result must NOT have triggered any callbacks.
  assert.equal(onTickCalls.length, 0, 'onTick must not be called with stale result')
  assert.equal(onTerminalCalls.length, 0, 'onTerminal must not be called with stale result')

  // No new timer was scheduled from the stale result.
  assert.equal(scheduler.pending.size, 0)
})

test('in-flight fetch result discarded after restart with new task', async () => {
  const scheduler = createFakeScheduler()
  let resolveFetch
  const onTickCalls = []
  const onTerminalCalls = []

  const coordinator = createPollingCoordinator({
    fetchJobStatus: () => new Promise((resolve) => { resolveFetch = resolve }),
    onTick(gt, record) { onTickCalls.push({ gt, status: record.status }) },
    onTerminal(gt) { onTerminalCalls.push(gt) },
    setTimeoutFn: scheduler.setTimeoutFn,
    clearTimeoutFn: scheduler.clearTimeoutFn,
    intervalMs: 100,
  })

  coordinator.start('ground', 'a'.repeat(32))
  assert.equal(scheduler.pending.size, 1)

  // Fire the tick WITHOUT awaiting -- the fetch blocks on a deferred promise.
  const [handle, entry] = scheduler.pending.entries().next().value
  scheduler.pending.delete(handle)
  const staleTickPromise = entry.fn()

  // While the fetch is pending, restart with a new task ID.
  coordinator.start('ground', 'b'.repeat(32))

  // Resolve the old (stale) fetch.
  resolveFetch({ id: 'a'.repeat(32), status: 'completed', progress: 100 })
  await staleTickPromise

  // Stale result ignored.
  assert.equal(onTickCalls.length, 0, 'onTick must not be called with stale result')
  assert.equal(onTerminalCalls.length, 0, 'onTerminal must not be called with stale result')

  // The new timer from restart is still pending (not from the stale result).
  assert.equal(scheduler.pending.size, 1)
})
