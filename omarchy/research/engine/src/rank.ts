// Relevance for search results: lexical (BM25 over title + snippet), fuzzy (character trigrams, which
// also catch Russian and Kazakh inflections), cross-engine agreement, source authority, freshness and
// diversity. Every signal is cheap enough to run on each search (well under a millisecond per result).

import {
  canonicalUrl,
  cosine,
  hostOf,
  isTechnical,
  isTimeSensitive,
  jaccard,
  terms,
  trigrams
} from './text'

export type Candidate = {
  title: string
  url: string
  content: string
  engines?: string[]
  score?: number // SearXNG's own merged score (higher when several engines agree)
  publishedDate?: string | null
  category?: string
}

export type Ranked = Candidate & {
  rank: number
  signals: Record<string, number>
  domain: string
}

// Primary and official sources. Matched against the host and its parent domains.
const AUTHORITY: Array<[RegExp, number]> = [
  [/(^|\.)(gov|gov\.[a-z]{2}|gouv\.fr|gc\.ca|europa\.eu|un\.org|who\.int|oecd\.org|worldbank\.org|imf\.org)$/, 1],
  [/(^|\.)(edu|ac\.[a-z]{2})$/, 0.8],
  [/(^|\.)(wikipedia\.org|wikidata\.org|britannica\.com)$/, 0.7],
  [/(^|\.)(nature\.com|science\.org|arxiv\.org|pubmed\.ncbi\.nlm\.nih\.gov|nih\.gov|ieee\.org|acm\.org)$/, 0.8],
  [/(^|\.)(reuters\.com|apnews\.com|bbc\.(com|co\.uk)|ft\.com|bloomberg\.com|nytimes\.com|theguardian\.com|wsj\.com|economist\.com|tass\.(ru|com)|interfax\.(ru|kz)|kazinform\.kz|tengrinews\.kz|inform\.kz|kommersant\.ru|rbc\.ru|vedomosti\.ru)$/, 0.6],
  [/(^|\.)(nvidia\.com|amd\.com|intel\.com|apple\.com|microsoft\.com|google\.com|blog\.google|openai\.com|anthropic\.com|meta\.com|mozilla\.org|archlinux\.org|kernel\.org|python\.org|nodejs\.org|rust-lang\.org|github\.com|gitlab\.com|docker\.com|kubernetes\.io)$/, 0.55]
]
// Hosts whose pages are official documentation for a technical question.
const DOCS = /(^|\.)(docs?|developer|developers|devdocs|learn|wiki|manual|man|reference|api)\.|readthedocs\.io$|github\.com$|man7\.org$|archlinux\.org$/
// Low-value for answering: content farms, aggregators, image boards, SEO spam patterns.
const LOW = /(^|\.)(pinterest\.[a-z.]+|quora\.com|answers\.com|ehow\.com|wikihow\.com|slideshare\.net|scribd\.com|coursehero\.com|chegg\.com|brainly\.[a-z.]+|facebook\.com|instagram\.com|tiktok\.com|vk\.com|ok\.ru|dzen\.ru)$/

export function authority(host: string, technical: boolean): number {
  let a = 0
  for (const [re, w] of AUTHORITY) if (re.test(host)) a = Math.max(a, w)
  if (technical && DOCS.test(host)) a = Math.max(a, 0.9)
  if (LOW.test(host)) a -= 0.6
  return a
}

export function freshness(published: string | null | undefined, now = Date.now()): number {
  if (!published) return 0
  const t = Date.parse(published)
  if (Number.isNaN(t) || t > now + 86_400_000) return 0
  const days = (now - t) / 86_400_000
  if (days < 2) return 1
  if (days < 14) return 0.8
  if (days < 60) return 0.55
  if (days < 365) return 0.3
  return 0
}

function bm25(queryTerms: string[], docs: string[][]): number[] {
  const N = docs.length || 1
  const avg = docs.reduce((s, d) => s + d.length, 0) / N || 1
  const df = new Map<string, number>()
  for (const d of docs) for (const t of new Set(d)) df.set(t, (df.get(t) || 0) + 1)
  const k1 = 1.2
  const b = 0.75
  return docs.map(d => {
    const tf = new Map<string, number>()
    for (const t of d) tf.set(t, (tf.get(t) || 0) + 1)
    let s = 0
    for (const q of new Set(queryTerms)) {
      const f = tf.get(q)
      if (!f) continue
      const n = df.get(q) || 0
      const idf = Math.log(1 + (N - n + 0.5) / (n + 0.5))
      s += (idf * f * (k1 + 1)) / (f + k1 * (1 - b + (b * d.length) / avg))
    }
    return s
  })
}

export function dedupe(items: Candidate[]): Candidate[] {
  const byUrl = new Map<string, Candidate>()
  for (const item of items) {
    if (!item.url || !/^https?:\/\//i.test(item.url)) continue
    const key = canonicalUrl(item.url)
    const prev = byUrl.get(key)
    if (!prev) {
      byUrl.set(key, { ...item, engines: [...(item.engines || [])] })
      continue
    }
    // The same page from two engines: keep the longer snippet, merge engines, keep the best score.
    prev.engines = [...new Set([...(prev.engines || []), ...(item.engines || [])])]
    if ((item.content || '').length > (prev.content || '').length) prev.content = item.content
    prev.score = Math.max(prev.score || 0, item.score || 0)
    prev.publishedDate = prev.publishedDate || item.publishedDate
  }
  // Near-identical titles on the same site (syndicated or paginated copies) count once.
  const out: Candidate[] = []
  for (const item of byUrl.values()) {
    const t = terms(item.title)
    const host = hostOf(item.url)
    if (out.some(o => hostOf(o.url) === host && jaccard(terms(o.title), t) > 0.85)) continue
    out.push(item)
  }
  return out
}

export function rank(
  query: string,
  items: Candidate[],
  opts: { limit: number; perDomain?: number; fresh?: boolean } = { limit: 10 }
): Ranked[] {
  const technical = isTechnical(query)
  const fresh = opts.fresh ?? isTimeSensitive(query)
  const q = terms(query)
  const qgrams = trigrams(query)
  const docs = items.map(i => terms(`${i.title} ${i.title} ${i.content}`))
  const lexical = bm25(q, docs)
  const maxLex = Math.max(1e-9, ...lexical)
  const maxScore = Math.max(1e-9, ...items.map(i => i.score || 0))

  const scored: Ranked[] = items.map((item, i) => {
    const host = hostOf(item.url)
    const covered = q.length ? q.filter(t => docs[i].includes(t)).length / new Set(q).size : 0
    const signals = {
      lexical: lexical[i] / maxLex,
      coverage: covered,
      fuzzy: cosine(qgrams, trigrams(`${item.title} ${item.content}`)),
      agreement: Math.min(1, ((item.engines?.length || 1) - 1) / 2) * 0.5 + ((item.score || 0) / maxScore) * 0.5,
      authority: authority(host, technical),
      freshness: fresh ? freshness(item.publishedDate) : 0,
      thin: (item.content || '').length < 40 ? 1 : 0
    }
    // For "newest/latest" questions an old page should not lead, however well its words match.
    const ageDays = item.publishedDate ? (Date.now() - Date.parse(item.publishedDate)) / 86_400_000 : NaN
    const stale = fresh && ageDays > 180 ? 1 : 0
    const rankScore =
      0.32 * signals.lexical +
      0.18 * signals.coverage +
      0.14 * signals.fuzzy +
      0.12 * signals.agreement +
      0.14 * signals.authority +
      (fresh ? 0.18 : 0) * signals.freshness -
      0.12 * signals.thin -
      0.15 * stale
    Object.assign(signals, { stale })
    return { ...item, domain: host, rank: rankScore, signals }
  })
  scored.sort((a, b) => b.rank - a.rank)
  // Results that share no word with the question and barely resemble it are noise; keep at least two results.
  const relevant = scored.filter(r => r.signals.coverage > 0 || r.signals.fuzzy >= 0.2)
  if (relevant.length >= 2) scored.splice(0, scored.length, ...relevant)

  // Diversity: at most `perDomain` results from one site in the selection; the rest only fill gaps.
  const perDomain = opts.perDomain ?? 2
  const picked: Ranked[] = []
  const overflow: Ranked[] = []
  const counts = new Map<string, number>()
  for (const r of scored) {
    const c = counts.get(r.domain) || 0
    if (c < perDomain) {
      picked.push(r)
      counts.set(r.domain, c + 1)
    } else overflow.push(r)
    if (picked.length >= opts.limit) break
  }
  for (const r of overflow) {
    if (picked.length >= opts.limit) break
    picked.push(r)
  }
  return picked
}
