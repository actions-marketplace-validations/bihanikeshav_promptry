import { useEffect, useState } from "react";

// Process-lived cache so navigating away and back (which unmounts pages)
// renders instantly from the last result instead of flashing a loader.
const _cache = new Map<string, unknown>();

/** Stale-while-revalidate fetch. Returns cached data synchronously on mount
 *  (no flicker on back/forward nav) and refreshes in the background. */
export function useCached<T>(key: string | null, fetcher: () => Promise<T>): { data: T | undefined; loading: boolean } {
  const [data, setData] = useState<T | undefined>(() => (key ? (_cache.get(key) as T | undefined) : undefined));

  useEffect(() => {
    if (key == null) return;
    let alive = true;
    const cached = _cache.get(key) as T | undefined;
    if (cached !== undefined) setData(cached); // instant from cache
    fetcher()
      .then((r) => { _cache.set(key, r); if (alive) setData(r); })
      .catch(() => {});
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return { data, loading: data === undefined };
}

/** Warm the cache for `key` ahead of navigation, so the destination page
 *  renders from cache without a loading flash. Fire-and-forget. */
export function prefetch<T>(key: string, fetcher: () => Promise<T>) {
  if (_cache.has(key)) return;
  fetcher().then((r) => _cache.set(key, r)).catch(() => {});
}

/** Drop cached entries whose key starts with `prefix` (after a write). */
export function invalidateCache(prefix: string) {
  for (const k of [..._cache.keys()]) if (k.startsWith(prefix)) _cache.delete(k);
}
