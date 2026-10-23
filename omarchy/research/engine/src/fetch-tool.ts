// The fetch tool the research agent uses: Morphic's tool contract (input schema, "fetching" then
// "complete" states, citation labels from the same allocator as search) on top of Morphic's SSRF-safe
// fetch, with Jarvis's extraction: the model gets the passages that answer the question, not 50 000
// characters of page, and every page is kept for citation checks.

import { tool } from 'ai'

import { INVALID_URL_SENTINEL, ToolFailureError } from '@/lib/errors/tool-error'
import { fetchSchema } from '@/lib/schema/fetch'
import { assignCitationLabels, type CitationLabelAllocator } from '@/lib/utils/citation'
import { assertPublicUrl, safeFetch } from '@/lib/utils/safe-fetch'

import { TtlCache } from './cache'
import { current, type Page, type RequestCtx } from './context'
import { htmlTitle, htmlToText, publishedDate, selectPassages } from './extract'
import { canonicalUrl, hostOf } from './text'
import { sanitizeUntrusted } from './untrusted'

const MAX_BYTES = 2_500_000
const MAX_TEXT = 60_000
export const pageCache = new TtlCache<Page>(Number(process.env.RESEARCH_PAGE_CACHE_ENTRIES || 200))
const PAGE_TTL = Number(process.env.RESEARCH_PAGE_TTL_MIN || 60) * 60_000

async function readCapped(response: Response, signal: AbortSignal): Promise<string> {
  const reader = response.body?.getReader()
  if (!reader) return ''
  const chunks: Uint8Array[] = []
  let size = 0
  while (true) {
    if (signal.aborted) throw new Error('aborted')
    const { done, value } = await reader.read()
    if (done) break
    size += value.byteLength
    chunks.push(value)
    if (size >= MAX_BYTES) {
      await reader.cancel().catch(() => {})
      break
    }
  }
  const charset = response.headers.get('content-type')?.match(/charset=([\w-]+)/i)?.[1] || 'utf-8'
  try {
    return new TextDecoder(charset, { fatal: false }).decode(Buffer.concat(chunks))
  } catch {
    return new TextDecoder('utf-8').decode(Buffer.concat(chunks))
  }
}

export async function fetchPage(url: string, ctx?: RequestCtx, fresh = false): Promise<Page> {
  if (!/^https?:\/\//i.test(url) || !URL.canParse(url)) throw new ToolFailureError('fetch', INVALID_URL_SENTINEL)
  try {
    assertPublicUrl(url)
  } catch (error) {
    throw new ToolFailureError('fetch', error)
  }
  const key = canonicalUrl(url)
  const known = ctx?.pages.get(key)
  if (known) return known
  if (!fresh && !ctx?.fresh) {
    const hit = pageCache.get(key)
    if (hit) {
      const page = { ...hit.value, cached: true }
      ctx?.pages.set(key, page)
      return page
    }
  }
  const timeout = ctx?.limits.fetchTimeoutMs || 5000
  const signal = AbortSignal.any([AbortSignal.timeout(timeout), ...(ctx?.signal ? [ctx.signal] : [])])
  const started = performance.now()
  let response: Response
  try {
    response = await safeFetch(url, {
      signal,
      headers: {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) JarvisResearch/1.0 (personal assistant; Morphic)',
        Accept: 'text/html,application/xhtml+xml,text/plain;q=0.9,application/json;q=0.8',
        'Accept-Language': ctx?.lang === 'ru' ? 'ru,en;q=0.7' : ctx?.lang === 'kk' ? 'kk,ru;q=0.8,en;q=0.6' : 'en,*;q=0.5'
      }
    })
  } catch (error) {
    if (signal.aborted && !ctx?.signal.aborted) throw new ToolFailureError('fetch', new Error(`Timed out after ${timeout} ms`))
    throw new ToolFailureError('fetch', error)
  }
  if (!response.ok) throw new ToolFailureError('fetch', new Error(`HTTP ${response.status}: ${response.statusText}`))
  const type = (response.headers.get('content-type') || '').split(';', 1)[0].trim().toLowerCase()
  const isHtml = type === 'text/html' || type === 'application/xhtml+xml' || type === ''
  const isText = type === 'text/plain' || type === 'application/json' || type.endsWith('+json') || type === 'text/markdown'
  if (!isHtml && !isText) {
    await response.body?.cancel().catch(() => {})
    throw new ToolFailureError('fetch', new Error(`Unsupported content type: ${type}`))
  }
  const body = await readCapped(response, signal)
  const page: Page = {
    url,
    title: (isHtml ? htmlTitle(body) : '') || hostOf(url),
    text: (isHtml ? htmlToText(body) : body).slice(0, MAX_TEXT),
    published: isHtml ? publishedDate(body) : null,
    fetchedAt: Date.now(),
    cached: false
  }
  if (ctx) ctx.timings.fetchMs.push(performance.now() - started)
  if (page.text.length > 80) pageCache.set(key, page, PAGE_TTL)
  ctx?.pages.set(key, page)
  return page
}

export function createJarvisFetchTool(options: { labelAllocator: CitationLabelAllocator; onPage?: (page: Page, label: string) => void }) {
  return tool({
    description:
      'Open a web page and read the passages relevant to the question (HTML and plain text; PDFs are not supported). ' +
      'Use it for the most promising search results when snippets are not enough to support a claim.',
    inputSchema: fetchSchema,
    async *execute({ url }) {
      const ctx = current()
      if (ctx && ctx.counters.fetches >= ctx.limits.fetches) {
        throw new ToolFailureError('fetch', new Error('Page budget for this answer is used up; answer from the sources you have.'))
      }
      if (ctx) ctx.counters.fetches++
      yield { state: 'fetching' as const, url }
      const page = await fetchPage(url, ctx)
      const clean = sanitizeUntrusted(selectPassages(page.text, ctx?.query || '', ctx?.mode === 'thorough' ? 3600 : 2400))
      const content = clean.text
      if (ctx) {
        ctx.visible.push(content)
        if (clean.flagged) {
          ctx.counters.injectionFlags += clean.flagged
          ctx.emit({ t: 'warning', kind: 'injection', message: `removed ${clean.flagged} sentence(s) addressed to AI assistants`, url: page.url })
        }
      }
      const [result] = assignCitationLabels(
        [{ title: page.title, url: page.url, content: page.published ? `(${page.published.slice(0, 10)}) ${content}` : content }],
        options.labelAllocator.reserve(1)
      )
      options.onPage?.(page, result.label as string)
      yield { state: 'complete' as const, results: [result], query: '', images: [] }
    },
    toModelOutput: ({ output }) => {
      const o = output as { results?: Array<Record<string, unknown>> }
      return {
        type: 'json',
        value: { results: (o?.results || []).map(r => ({ url: r.url, title: r.title, content: r.content, label: r.label })) } as never
      }
    }
  })
}
