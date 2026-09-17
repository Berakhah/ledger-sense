/**
 * Only the latest-issued request may commit its result.
 *
 * The review queue fires a refetch after every write and a full resync after
 * every failed one; the network is free to answer those out of order, and a
 * stale answer that lands last must not overwrite a fresher one. Each call
 * takes a sequence number; when its promise settles, it is `latest` only if
 * nothing newer was issued through the same guard in the meantime.
 *
 * Rejections pass through untouched, so the caller still learns a request
 * failed even when it was superseded.
 */

export type Guarded<T> = { latest: true; value: T } | { latest: false }

export function latestOnly(): <T>(request: Promise<T>) => Promise<Guarded<T>> {
  let issued = 0
  return async <T>(request: Promise<T>): Promise<Guarded<T>> => {
    const mine = ++issued
    const value = await request
    return mine === issued ? { latest: true, value } : { latest: false }
  }
}
