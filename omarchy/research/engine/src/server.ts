// The research engine as a local service for jarvisd: HTTP over a Unix socket (or 127.0.0.1 with a
// bearer token), one NDJSON stream per research request. Nothing listens on a public interface.

import { chmodSync, existsSync, unlinkSync } from 'node:fs'
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'

import { pageCache, fetchPage } from './fetch-tool'
import { selectPassages } from './extract'
import { activeRuns, MODES, research, type ResearchRequest } from './research'
import { engineStatus, runSearch, searchCache } from './searxng'
import type { Mode, RequestCtx } from './context'
import { detectLang } from './text'

const VERSION = '1.0.0'
const MORPHIC = { tag: 'v1.8.0', commit: 'a95b1d7aa17f729fd8cae7e9896fa8d32b76989d' }
const started = Date.now()
const runs = new Map<string, AbortController>()
const stats = { requests: 0, completed: 0, failed: 0, cancelled: 0 }

function send(res: ServerResponse, status: number, body: unknown): void {
  const text = JSON.stringify(body)
  res.writeHead(status, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(text) })
  res.end(text)
}

async function readJson(req: IncomingMessage, limit = 256 * 1024): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = []
  let size = 0
  for await (const chunk of req) {
    size += (chunk as Buffer).length
    if (size > limit) throw new Error('request too large')
    chunks.push(chunk as Buffer)
  }
  const text = Buffer.concat(chunks).toString('utf8')
  return text ? JSON.parse(text) : {}
}

let searxProbe = { at: 0, ok: false, ms: 0, error: '' }
async function searxHealth() {
  if (Date.now() - searxProbe.at < 10_000) return searxProbe
  const base = (process.env.SEARXNG_API_URL || 'http://127.0.0.1:18890').replace(/\/+$/, '')
  const t = performance.now()
  try {
    const r = await fetch(`${base}/healthz`, { signal: AbortSignal.timeout(2000) })
    searxProbe = { at: Date.now(), ok: r.ok, ms: Math.round(performance.now() - t), error: r.ok ? '' : `HTTP ${r.status}` }
  } catch (error) {
    searxProbe = { at: Date.now(), ok: false, ms: 0, error: error instanceof Error ? error.message : String(error) }
  }
  return searxProbe
}

function modelConfigured(): { model: string; ready: boolean } {
  const model = process.env.RESEARCH_MODEL || 'google:gemini-3.5-flash'
  const provider = model.split(':')[0]
  const ready =
    provider === 'google'
      ? Boolean(process.env.GOOGLE_GENERATIVE_AI_API_KEY)
      : provider === 'openai-compatible'
        ? Boolean(process.env.OPENAI_COMPATIBLE_API_BASE_URL)
        : provider === 'ollama'
          ? Boolean(process.env.OLLAMA_BASE_URL)
          : provider === 'openai'
            ? Boolean(process.env.OPENAI_API_KEY)
            : provider === 'anthropic'
              ? Boolean(process.env.ANTHROPIC_API_KEY)
              : false
  return { model, ready }
}

function searchCtx(body: Record<string, unknown>, signal: AbortSignal): RequestCtx {
  const query = String(body.query || '')
  const mode = (['instant', 'adaptive', 'thorough'].includes(String(body.mode)) ? body.mode : 'instant') as Mode
  return {
    id: 'search',
    mode,
    lang: detectLang(query),
    fresh: Boolean(body.fresh),
    scope: body.private && body.conversation ? `conv:${body.conversation}` : 'global',
    query,
    signal,
    emit: () => {},
    limits: { ...MODES[mode], results: Math.min(20, Number(body.limit || 8)) },
    counters: { searches: 0, fetches: 0, cacheHits: 0, engineFailures: 0, fallbacks: 0, injectionFlags: 0 },
    sources: new Map(),
    pages: new Map(),
    searches: new Map(),
    timings: { searchMs: [], fetchMs: [] },
    visible: []
  }
}

async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const token = process.env.JARVIS_RESEARCH_TOKEN
  if (token && req.headers.authorization !== `Bearer ${token}`) return send(res, 401, { error: 'unauthorized' })
  const url = new URL(req.url || '/', 'http://engine')

  if (req.method === 'GET' && url.pathname === '/health') {
    const searx = await searxHealth()
    const model = modelConfigured()
    return send(res, 200, {
      ok: searx.ok && model.ready,
      version: VERSION,
      morphic: MORPHIC,
      uptime_s: Math.round((Date.now() - started) / 1000),
      searxng: { ok: searx.ok, ms: searx.ms, error: searx.error, url: process.env.SEARXNG_API_URL || 'http://127.0.0.1:18890' },
      model,
      runs: activeRuns(),
      rss_mb: Math.round(process.memoryUsage().rss / 1048576)
    })
  }
  if (req.method === 'GET' && url.pathname === '/v1/stats') {
    return send(res, 200, { stats, engines: engineStatus(), search_cache: searchCache.stats(), page_cache: pageCache.stats(), runs: activeRuns() })
  }
  if (req.method !== 'POST') return send(res, 404, { error: 'not found' })
  let body: Record<string, unknown>
  try {
    body = await readJson(req)
  } catch (error) {
    return send(res, 400, { error: error instanceof Error ? error.message : 'bad request' })
  }

  if (url.pathname === '/v1/research') {
    if (!String(body.query || '').trim()) return send(res, 400, { error: 'query is required' })
    const id = String(body.id || Math.random().toString(36).slice(2, 10))
    const controller = new AbortController()
    runs.get(id)?.abort()
    runs.set(id, controller)
    stats.requests++
    // The client hanging up (Jarvis cancelled, or went away) stops the work: searches, fetches, model.
    res.on('close', () => {
      if (!res.writableFinished) controller.abort(new Error('client closed'))
    })
    res.writeHead(200, { 'Content-Type': 'application/x-ndjson', 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' })
    let outcome = 'failed'
    try {
      for await (const event of research({ ...(body as ResearchRequest), id }, controller.signal)) {
        if (event.t === 'final') outcome = event.ok ? 'completed' : 'failed'
        if (event.t === 'cancelled') outcome = 'cancelled'
        if (!res.writableEnded) res.write(`${JSON.stringify(event)}\n`)
      }
    } catch (error) {
      if (!res.writableEnded) res.write(`${JSON.stringify({ t: 'error', message: error instanceof Error ? error.message : String(error) })}\n`)
    } finally {
      if (controller.signal.aborted && outcome !== 'completed') outcome = 'cancelled'
      stats[outcome as 'completed' | 'failed' | 'cancelled']++
      runs.delete(id)
      res.end()
    }
    return
  }
  if (url.pathname === '/v1/cancel') {
    const ids = body.id ? [String(body.id)] : [...runs.keys()]
    for (const id of ids) runs.get(id)?.abort(new Error('cancelled'))
    return send(res, 200, { ok: true, cancelled: ids.filter(id => runs.has(id)) })
  }
  if (url.pathname === '/v1/search') {
    const query = String(body.query || '').trim()
    if (!query) return send(res, 400, { error: 'query is required' })
    const controller = new AbortController()
    res.on('close', () => controller.abort())
    try {
      const ctx = searchCtx(body, controller.signal)
      const outcome = await runSearch(query, { limit: ctx.limits.results, ctx, images: Boolean(body.images) })
      return send(res, 200, {
        ok: true,
        query,
        cached: outcome.cached,
        stored_at: outcome.storedAt,
        engines: outcome.engines,
        failed_engines: outcome.failed,
        fallback: outcome.fallback,
        sent: outcome.sent,
        ms: Math.round(outcome.ms),
        results: outcome.ranked.slice(0, ctx.limits.results).map(r => ({
          title: r.title,
          url: r.url,
          snippet: r.content,
          domain: r.domain,
          published: r.publishedDate || null,
          engines: r.engines || [],
          rank: Math.round(r.rank * 1000) / 1000
        })),
        images: outcome.images
      })
    } catch (error) {
      return send(res, 502, { ok: false, error: error instanceof Error ? error.message : String(error) })
    }
  }
  if (url.pathname === '/v1/fetch') {
    const controller = new AbortController()
    res.on('close', () => controller.abort())
    try {
      const ctx = searchCtx(body, controller.signal)
      const page = await fetchPage(String(body.url || ''), ctx, Boolean(body.fresh))
      return send(res, 200, {
        ok: true,
        url: page.url,
        title: page.title,
        published: page.published,
        cached: page.cached,
        fetched_at: page.fetchedAt,
        excerpt: selectPassages(page.text, String(body.query || ''), Number(body.budget || 2400))
      })
    } catch (error) {
      return send(res, 502, { ok: false, error: error instanceof Error ? error.message : String(error) })
    }
  }
  if (url.pathname === '/v1/cache/clear') {
    const scope = String(body.scope || '')
    const n = scope ? searchCache.clearPrefix(`${scope}|`) : searchCache.clearPrefix('')
    return send(res, 200, { ok: true, removed: n })
  }
  return send(res, 404, { error: 'not found' })
}

const server = createServer((req, res) => {
  handle(req, res).catch(error => {
    if (!res.headersSent) send(res, 500, { error: error instanceof Error ? error.message : String(error) })
    else res.end()
  })
})
server.keepAliveTimeout = 30_000
server.headersTimeout = 15_000
server.requestTimeout = 0 // research streams can be long; each has its own time budget

const socketPath = process.env.JARVIS_RESEARCH_SOCKET
if (socketPath) {
  if (existsSync(socketPath)) unlinkSync(socketPath)
  server.listen(socketPath, () => {
    chmodSync(socketPath, 0o600)
    console.log(`jarvis research engine ${VERSION} (Morphic ${MORPHIC.tag}) on ${socketPath}`)
  })
} else {
  const port = Number(process.env.JARVIS_RESEARCH_PORT || 18891)
  if (!process.env.JARVIS_RESEARCH_TOKEN) {
    console.error('JARVIS_RESEARCH_TOKEN is required when listening on TCP')
    process.exit(2)
  }
  server.listen(port, '127.0.0.1', () => console.log(`jarvis research engine ${VERSION} (Morphic ${MORPHIC.tag}) on 127.0.0.1:${port}`))
}

function shutdown(signal: string) {
  console.log(`${signal}: stopping (${runs.size} running)`)
  for (const c of runs.values()) c.abort(new Error('shutting down'))
  server.close(() => process.exit(0))
  setTimeout(() => process.exit(0), 3000).unref()
}
process.on('SIGTERM', () => shutdown('SIGTERM'))
process.on('SIGINT', () => shutdown('SIGINT'))
process.on('unhandledRejection', error => console.error('unhandled rejection:', error))
