// A small TTL + LRU cache. Entries remember when they were stored, so answers can tell cached evidence
// from evidence retrieved for this request.

export type Cached<T> = { value: T; storedAt: number; expiresAt: number }

export class TtlCache<T> {
  private map = new Map<string, Cached<T>>()
  hits = 0
  misses = 0

  constructor(private max = 500) {}

  get(key: string, now = Date.now()): Cached<T> | undefined {
    const entry = this.map.get(key)
    if (!entry) {
      this.misses++
      return undefined
    }
    if (entry.expiresAt <= now) {
      this.map.delete(key)
      this.misses++
      return undefined
    }
    this.map.delete(key)
    this.map.set(key, entry)
    this.hits++
    return entry
  }

  set(key: string, value: T, ttlMs: number, now = Date.now()): void {
    if (ttlMs <= 0) return
    this.map.delete(key)
    this.map.set(key, { value, storedAt: now, expiresAt: now + ttlMs })
    while (this.map.size > this.max) this.map.delete(this.map.keys().next().value as string)
  }

  delete(key: string): void {
    this.map.delete(key)
  }

  clearPrefix(prefix: string): number {
    let n = 0
    for (const key of [...this.map.keys()]) {
      if (key.startsWith(prefix)) {
        this.map.delete(key)
        n++
      }
    }
    return n
  }

  get size(): number {
    return this.map.size
  }

  stats() {
    return { size: this.map.size, max: this.max, hits: this.hits, misses: this.misses }
  }
}
