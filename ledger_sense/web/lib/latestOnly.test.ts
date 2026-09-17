import { describe, expect, it } from 'vitest'
import { latestOnly } from './latestOnly'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}

describe('latestOnly', () => {
  it('lets the only request commit', async () => {
    const guard = latestOnly()
    const a = deferred<number>()
    const run = guard(a.promise)
    a.resolve(1)
    expect(await run).toEqual({ latest: true, value: 1 })
  })

  it('drops an earlier request that resolves after a later one was issued', async () => {
    // Two overlapping fetches; the older one answers LAST. Only the newer
    // may commit, even though it answered first.
    const guard = latestOnly()
    const first = deferred<string>()
    const second = deferred<string>()
    const firstRun = guard(first.promise)
    const secondRun = guard(second.promise)
    second.resolve('fresh')
    first.resolve('stale')
    expect(await secondRun).toEqual({ latest: true, value: 'fresh' })
    expect(await firstRun).toEqual({ latest: false })
  })

  it('drops an earlier request even when it resolves first', async () => {
    const guard = latestOnly()
    const first = deferred<string>()
    const firstRun = guard(first.promise)
    const second = deferred<string>()
    const secondRun = guard(second.promise)
    first.resolve('stale')
    expect(await firstRun).toEqual({ latest: false })
    second.resolve('fresh')
    expect(await secondRun).toEqual({ latest: true, value: 'fresh' })
  })

  it('a rejected request still rejects, so the caller can report it', async () => {
    const guard = latestOnly()
    await expect(guard(Promise.reject(new Error('boom')))).rejects.toThrow('boom')
  })

  it('keeps separate guards independent', async () => {
    const a = latestOnly()
    const b = latestOnly()
    const runA = a(Promise.resolve(1))
    const runB = b(Promise.resolve(2))
    expect(await runA).toEqual({ latest: true, value: 1 })
    expect(await runB).toEqual({ latest: true, value: 2 })
  })
})
