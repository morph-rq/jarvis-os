// Jarvis's search provider for Morphic: SearXNG with per-language engine groups, health-aware engine
// choice, failover, retries, bounded concurrency, caching, de-duplication and ranking. It implements
// Morphic's SearchProvider interface, so Morphic's search tool (labels, model-facing projection,
// streaming states) runs unchanged on top of it.

import { isIP } from 'node:net'

import type { SearchResults } from '@/lib/types'
import { BaseSearchProvider } from '@/lib/tools/search/providers/base'
import { isBlockedAddress } from '@/lib/utils/safe-fetch'

import { TtlCache } from './cache'
import { sanitizeUntrusted } from './untrusted'
import { current, type RequestCtx } from './context'
import { type Candidate, dedupe, rank, type Ranked } from './rank'
import { cacheKeyText, detectLang, hostOf, isTechnical, isTimeSensitive, type Lang, redactForSearch, snippetDate } from './text'

type Group = { primary: string[]; fallback: string[] }

const DEFAULT_GROUPS: Record<string, Group> = {
  en: { primary: ['duckduckgo', 'brave', 'startpage', 'mojeek', 'wikipedia'], fallback: ['google', 'bing', 'qwant'] },
  ru: { primary: ['yandex', 'duckduckgo', 'brave', 'startpage', 'wikipedia'], fallback: ['google', 'bing', 'mojeek'] },
  kk: { primary: ['yandex', 'google', 'duckduckgo', 'wikipedia'], fallback: ['brave', 'bing', 'startpage'] },
  news: {
    primary: ['duckduckgo news', 'brave.news', 'startpage news', 'mojeek news'],
    fallback: ['bing news', 'google news', 'qwant news']
  },
  images: { primary: ['duckduckgo images', 'brave.images', 'startpage images'], fallback: ['bing images'] }
}

function groups(): Record<string, Group> {
  const raw = process.env.RESEARCH_ENGINE_GROUPS
  if (!raw) return DEFAULT_GROUPS
  try {
    return { ...DEFAULT_GROUPS, ...JSON.parse(raw) }
  } catch {
    return DEFAULT_GROUPS
  }
}

// ---------------------------------------------------------------- engine health

type Health = { ok: number; failed: number; streak: number; suspendedUntil: number; lastError: string; lastMs: number }
const health = new Map<string, Health>()
const SUSPEND_MS = Number(process.env.RESEARCH_ENGINE_SUSPEND_MS || 120_000)

function engineHealth(name: string): Health {
  let h = health.get(name)
  if (!h) {
    h = { ok: 0, failed: 0, streak: 0, suspendedUntil: 0, lastError: '', lastMs: 0 }
    health.set(name, h)
  }
  return h
}

function noteEngine(name: string, ok: boolean, error = ''): void {
  const h = engineHealth(name)
  if (ok) {
    h.ok++
    h.streak = 0
    h.suspendedUntil = 0
  } else {
    h.failed++
    h.streak++
    h.lastError = error.slice(0, 120)
    // Two failures in a row: skip the engine for a while, so a blocked engine costs nothing per search.
    if (h.streak >= 2) h.suspendedUntil = Date.now() + SUSPEND_MS * Math.min(4, h.streak - 1)
  }
}

export function engineStatus() {
  const now = Date.now()
  return Object.fromEntries(
    [...health.entries()].map(([name, h]) => [
      name,
      { ok: h.ok, failed: h.failed, suspended: h.suspendedUntil > now, last_error: h.lastError }
    ])
  )
}

function usable(names: string[]): string[] {
  const now = Date.now()
  return names.filter(n => engineHealth(n).suspendedUntil <= now)
}

// ---------------------------------------------------------------- concurrency

const MAX_CONCURRENT = Number(process.env.RESEARCH_SEARX_CONCURRENCY || 8)
let active = 0
const waiting: Array<() => void> = []

async function slot<T>(fn: () => Promise<T>): Promise<T> {
  if (active >= MAX_CONCURRENT) await new Promise<void>(resolve => waiting.push(resolve))
  active++
  try {
    return await fn()
  } finally {
    active--
    waiting.shift()?.()
  }
}

// ---------------------------------------------------------------- one SearXNG request

type SearxResult = {
  title?: string
  url?: string
  content?: string
  engines?: string[]
  engine?: string
  score?: number
  publishedDate?: string | null
  img_src?: string
  thumbnail_src?: string
  category?: string
}
type SearxResponse = { results?: SearxResult[]; unresponsive_engines?: Array<[string, string]> }

export class SearxError extends Error {
  constructor(
    message: string,
    readonly status = 0
  ) {
    super(message)
  }
}

function baseUrl(): string {
  return (process.env.SEARXNG_API_URL || 'http://127.0.0.1:18890').replace(/\/+$/, '')
}

async function searxOnce(
  query: string,
  params: { engines: string[]; lang: Lang; timeRange?: string; signal?: AbortSignal; timeoutMs: number }
): Promise<{ results: SearxResult[]; unresponsive: Array<[string, string]>; ms: number }> {
  const url = new URL(`${baseUrl()}/search`)
  url.searchParams.set('q', query)
  url.searchParams.set('format', 'json')
  url.searchParams.set('engines', params.engines.join(','))
  url.searchParams.set('language', params.lang === 'other' ? 'all' : params.lang)
  url.searchParams.set('safesearch', '0')
  url.searchParams.set('pageno', '1')
  if (params.timeRange) url.searchParams.set('time_range', params.timeRange)
  const signals = [AbortSignal.timeout(params.timeoutMs)]
  if (params.signal) signals.push(params.signal)
  const started = performance.now()
  const response = await slot(() =>
    fetch(url, { headers: { Accept: 'application/json', 'X-Real-IP': '127.0.0.1' }, signal: AbortSignal.any(signals) })
  )
  if (!response.ok) throw new SearxError(`SearXNG HTTP ${response.status}`, response.status)
  const data = (await response.json()) as SearxResponse
  return { results: data.results || [], unresponsive: data.unresponsive_engines || [], ms: performance.now() - started }
}

async function searx(
  query: string,
  params: { engines: string[]; lang: Lang; timeRange?: string; signal?: AbortSignal; timeoutMs: number }
) {
  try {
    return await searxOnce(query, params)
  } catch (error) {
    if (params.signal?.aborted) throw error
    // Our own time limit ran out: SearXNG already waited on every engine, so asking again only doubles the wait.
    if (error instanceof Error && (error.name === 'TimeoutError' || error.name === 'AbortError')) throw error
    const status = error instanceof SearxError ? error.status : 0
    if (status && status < 500 && status !== 429) throw error
    // One retry for a dropped connection, a 5xx or a 429, after a short pause.
    await new Promise(resolve => setTimeout(resolve, status === 429 ? 800 : 250))
    return await searxOnce(query, params)
  }
}

type SearxReply = { results: SearxResult[]; unresponsive: Array<[string, string]>; ms: number }

// Each engine asked on its own, all at once; the answer is ready when enough engines have answered (or at a
// soft deadline with whatever arrived), so one slow engine never holds the rest. Every engine is still asked
// exactly once, as a combined SearXNG request would. Late answers are dropped.
async function raceEngines(
  query: string,
  engines: string[],
  params: { lang: Lang; timeRange?: string; signal?: AbortSignal; timeoutMs: number },
  settle: { minEngines: number; minResults: number; softMs: number }
): Promise<SearxReply> {
  if (engines.length <= 1) return searx(query, { ...params, engines })
  const started = performance.now()
  return new Promise<SearxReply>((resolve, reject) => {
    const results: SearxResult[] = []
    const unresponsive: Array<[string, string]> = []
    let answered = 0
    let settled = 0
    let done = false
    let lastError: unknown = null
    const finish = () => {
      if (done) return
      done = true
      clearTimeout(soft)
      if (!results.length && settled === engines.length && lastError && !unresponsive.length) reject(lastError)
      else resolve({ results, unresponsive, ms: performance.now() - started })
    }
    const soft = setTimeout(() => {
      if (results.length) finish()
    }, settle.softMs)
    for (const engine of engines) {
      searx(query, { ...params, engines: [engine] })
        .then(r => {
          if (done) return
          if (r.results.length) answered++
          results.push(...r.results)
          unresponsive.push(...r.unresponsive)
        })
        .catch(error => {
          if (done) return
          lastError = error
          if (!(error instanceof Error && (error.name === 'TimeoutError' || error.name === 'AbortError'))) {
            unresponsive.push([engine, error instanceof Error ? error.message : String(error)])
          }
        })
        .finally(() => {
          settled++
          if (done) return
          if ((answered >= settle.minEngines && results.length >= settle.minResults) || settled === engines.length) finish()
        })
    }
  })
}

function account(engines: string[], response: { results: SearxResult[]; unresponsive: Array<[string, string]> }) {
  const failed = new Map(response.unresponsive.map(([name, why]) => [name, why]))
  const answered = new Set(response.results.flatMap(r => r.engines || (r.engine ? [r.engine] : [])))
  for (const name of engines) {
    if (failed.has(name)) noteEngine(name, false, failed.get(name) || 'error')
    else if (answered.has(name)) noteEngine(name, true)
  }
  return [...failed.keys()]
}

function toCandidate(r: SearxResult): Candidate | null {
  if (!r.url || !r.title) return null
  let host: string
  try {
    const u = new URL(r.url)
    if (u.protocol !== 'https:' && u.protocol !== 'http:') return null
    host = u.hostname.replace(/^\[|\]$/g, '')
  } catch {
    return null
  }
  // A result pointing at a private address is never shown or opened (SSRF via search results).
  if (process.env.FETCH_ALLOW_PRIVATE_NETWORK !== 'true') {
    if (isIP(host) !== 0 && isBlockedAddress(host)) return null
    if (host === 'localhost' || /\.(local|localhost|internal|lan|home\.arpa)$/i.test(host)) return null
  }
  return {
    title: r.title.trim().slice(0, 300),
    url: r.url,
    content: (r.content || '').trim().slice(0, 1200),
    engines: r.engines || (r.engine ? [r.engine] : []),
    score: r.score,
    publishedDate: r.publishedDate || snippetDate(r.content || ''),
    category: r.category
  }
}

// ---------------------------------------------------------------- cache

export type CachedSearch = { ranked: Ranked[]; images: Array<{ url: string; title?: string; sourceUrl?: string; description: string }>; at: number }

export const searchCache = new TtlCache<CachedSearch>(Number(process.env.RESEARCH_CACHE_ENTRIES || 600))

function ttlFor(query: string): number {
  const minutes = (name: string, fallback: number) => Number(process.env[name] || fallback) * 60_000
  if (isTimeSensitive(query)) return minutes('RESEARCH_TTL_FRESH_MIN', 10)
  if (isTechnical(query)) return minutes('RESEARCH_TTL_TECHNICAL_MIN', 720)
  return minutes('RESEARCH_TTL_DEFAULT_MIN', 360)
}

// ---------------------------------------------------------------- the provider

export type SearchOutcome = CachedSearch & {
  cached: boolean
  storedAt: number
  engines: string[]
  failed: string[]
  fallback: boolean
  ms: number
  sent: string // the query the engines actually received (private details removed)
}

export async function runSearch(
  rawQuery: string,
  opts: {
    limit: number
    includeDomains?: string[]
    excludeDomains?: string[]
    ctx?: RequestCtx
    images?: boolean
  }
): Promise<SearchOutcome> {
  const ctx = opts.ctx
  const query = redactForSearch(rawQuery) || rawQuery
  const lang = ctx?.lang && ctx.lang !== 'other' ? ctx.lang : detectLang(query)
  const fresh = isTimeSensitive(query) || isTimeSensitive(ctx?.query || '')
  const include = (opts.includeDomains || []).map(d => d.toLowerCase().replace(/^www\./, ''))
  const exclude = (opts.excludeDomains || []).map(d => d.toLowerCase().replace(/^www\./, ''))
  const scope = ctx?.scope || 'global'
  const key = [scope, lang, fresh ? 'f' : 'n', cacheKeyText(query), include.join(','), exclude.join(',')].join('|')

  if (!ctx?.fresh) {
    const hit = searchCache.get(key)
    if (hit) {
      if (ctx) ctx.counters.cacheHits++
      return { ...hit.value, cached: true, storedAt: hit.storedAt, engines: [], failed: [], fallback: false, ms: 0, sent: query }
    }
  }

  const g = groups()
  const group = g[lang] || g.en
  const timeoutMs = ctx?.limits.searchTimeoutMs || 4000
  const signal = ctx?.signal
  // A single-site request goes to the engines as "site:"; several are filtered after the search.
  const q = include.length === 1 ? `${query} site:${include[0]}` : query
  const started = performance.now()

  const primary = usable(group.primary)
  const engines = primary.length >= 2 ? primary : [...primary, ...usable(group.fallback)].slice(0, 4)
  const mode = ctx?.mode || 'instant'
  const settle = {
    minEngines: 2,
    minResults: Math.min(6, opts.limit),
    softMs: Number(process.env.RESEARCH_SEARCH_SOFT_MS || (mode === 'instant' ? 1400 : mode === 'adaptive' ? 2200 : 3500))
  }
  const asks: Array<Promise<SearxReply>> = [
    raceEngines(q, engines.length ? engines : group.primary, { lang, signal, timeoutMs }, settle)
  ]
  // Time-sensitive questions also ask the news engines, in parallel, limited to the last month.
  const newsEngines = usable(g.news.primary)
  if (fresh && newsEngines.length) {
    asks.push(
      raceEngines(q, newsEngines, { lang, signal, timeoutMs, timeRange: 'month' }, { ...settle, minEngines: 1, minResults: 3 }).catch(
        () => ({ results: [], unresponsive: [], ms: 0 })
      )
    )
  }
  // Images ride along for answers that can show them; they never delay the text results.
  let imagePromise: Promise<SearxResult[]> | null = null
  if (opts.images) {
    imagePromise = searx(query, { engines: usable(g.images.primary).slice(0, 2), lang, signal, timeoutMs })
      .then(r => r.results)
      .catch(() => [])
  }

  const settled = await Promise.allSettled(asks)
  if (signal?.aborted) throw new Error('aborted')
  const failed: string[] = []
  let raw: SearxResult[] = []
  settled.forEach((s, i) => {
    if (s.status === 'fulfilled') {
      failed.push(...account(i === 0 ? engines : newsEngines, s.value))
      raw = raw.concat(s.value.results.filter(r => !r.img_src))
    }
  })
  if (settled[0].status === 'rejected' && settled.every(s => s.status === 'rejected')) {
    throw (settled[0] as PromiseRejectedResult).reason
  }

  let candidates = dedupe(raw.map(toCandidate).filter((c): c is Candidate => c !== null))
  let fallback = false
  // Failover: too few results means the engines asked were blocked, rate-limited or empty.
  if (candidates.length < Math.min(3, opts.limit)) {
    const backup = usable(group.fallback).filter(n => !engines.includes(n))
    if (backup.length) {
      fallback = true
      if (ctx) ctx.counters.fallbacks++
      try {
        const r = await raceEngines(q, backup, { lang, signal, timeoutMs }, { ...settle, minEngines: 1 })
        failed.push(...account(backup, r))
        candidates = dedupe([...candidates, ...r.results.filter(x => !x.img_src).map(toCandidate).filter((c): c is Candidate => c !== null)])
      } catch (error) {
        if (signal?.aborted) throw error
      }
    }
  }
  if (ctx) ctx.counters.engineFailures += failed.length

  if (include.length) candidates = candidates.filter(c => include.some(d => hostOf(c.url).endsWith(d)))
  if (exclude.length) candidates = candidates.filter(c => !exclude.some(d => hostOf(c.url).endsWith(d)))

  const ranked = rank(query, candidates, { limit: Math.max(opts.limit, 12), fresh })
  let images: CachedSearch['images'] = []
  if (imagePromise) {
    // Wait a little for images, never long.
    const imgs = await Promise.race([imagePromise, new Promise<SearxResult[]>(r => setTimeout(() => r([]), 400))])
    images = imgs
      .filter(r => r.img_src && /^https:\/\//.test(r.img_src))
      .slice(0, 8)
      .map(r => ({ url: r.img_src as string, title: r.title || undefined, sourceUrl: r.url || undefined, description: r.title || '' }))
  }
  const value: CachedSearch = { ranked, images, at: Date.now() }
  if (ranked.length) searchCache.set(key, value, ttlFor(query))
  return {
    ...value,
    cached: false,
    storedAt: value.at,
    engines,
    failed: [...new Set(failed)],
    fallback,
    ms: performance.now() - started,
    sent: q
  }
}

// Morphic's SearchProvider: what Morphic's search tool calls. The request context decides how many
// results fit the mode; the model sees each result's date, since recency matters for many answers.
export class JarvisSearxngProvider extends BaseSearchProvider {
  async search(
    query: string,
    maxResults: number = 10,
    _searchDepth: 'basic' | 'advanced' = 'basic',
    includeDomains: string[] = [],
    excludeDomains: string[] = []
  ): Promise<SearchResults> {
    const ctx = current()
    const limit = Math.min(maxResults, ctx?.limits.results || maxResults)
    const outcome = await runSearch(query, {
      limit,
      includeDomains,
      excludeDomains,
      ctx,
      images: ctx ? ctx.mode !== 'instant' && process.env.RESEARCH_IMAGES !== '0' : false
    })
    ctx?.emit({
      t: 'search',
      query,
      sent: outcome.sent,
      engines: outcome.engines,
      failed: outcome.failed,
      fallback: outcome.fallback,
      cached: outcome.cached,
      ms: Math.round(outcome.ms),
      results: outcome.ranked.length
    })
    if (ctx && !outcome.cached) ctx.timings.searchMs.push(outcome.ms)
    const results = outcome.ranked.slice(0, limit).map(r => {
      const clean = sanitizeUntrusted(r.content)
      if (clean.flagged && ctx) {
        ctx.counters.injectionFlags += clean.flagged
        ctx.emit({ t: 'warning', kind: 'injection', message: `removed ${clean.flagged} sentence(s) addressed to AI assistants`, url: r.url })
      }
      ctx?.visible.push(clean.text)
      return {
      title: r.title,
      url: r.url,
      content: r.publishedDate ? `(${r.publishedDate.slice(0, 10)}) ${clean.text}` : clean.text,
      // Kept for Jarvis, not shown to the model (Morphic's projection keeps url/title/content/label):
      snippet: clean.text,
      published: r.publishedDate || null,
      engines: r.engines || [],
      domain: r.domain,
      rank: Math.round(r.rank * 1000) / 1000,
      cached: outcome.cached,
      retrievedAt: outcome.storedAt,
      searchQuery: query
      }
    })
    return { results, images: outcome.images, query, number_of_results: results.length } as SearchResults
  }
}
