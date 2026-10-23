// Reading a page: the main text without navigation, scripts or boilerplate, its title and publication
// date, and the passages that matter for the question (the model gets those, not the whole page).

import { terms, trigrams, cosine } from './text'

const ENTITIES: Record<string, string> = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', mdash: '—', ndash: '–', hellip: '…',
  laquo: '«', raquo: '»', rsquo: '’', lsquo: '‘', rdquo: '”', ldquo: '“', copy: '©', reg: '®', deg: '°'
}

export function decodeEntities(s: string): string {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, code: string) => {
    if (code[0] === '#') {
      const n = code[1].toLowerCase() === 'x' ? parseInt(code.slice(2), 16) : parseInt(code.slice(1), 10)
      return Number.isFinite(n) && n > 0 && n < 0x110000 ? String.fromCodePoint(n) : ''
    }
    return ENTITIES[code.toLowerCase()] ?? m
  })
}

function meta(html: string, names: string[]): string | null {
  for (const name of names) {
    const re = new RegExp(
      `<meta[^>]+(?:name|property|itemprop)\\s*=\\s*["']${name.replace(/[.:]/g, '\\$&')}["'][^>]*>`,
      'i'
    )
    const tag = html.match(re)?.[0]
    const content = tag?.match(/content\s*=\s*["']([^"']*)["']/i)?.[1]
    if (content) return decodeEntities(content.trim())
  }
  return null
}

export function publishedDate(html: string): string | null {
  const candidates = [
    meta(html, ['article:published_time', 'og:published_time', 'datePublished', 'pubdate', 'publishdate', 'date', 'dc.date', 'DC.date.issued', 'article:modified_time']),
    html.match(/"datePublished"\s*:\s*"([^"]+)"/)?.[1] ?? null,
    html.match(/<time[^>]+datetime\s*=\s*["']([^"']+)["']/i)?.[1] ?? null
  ]
  for (const c of candidates) {
    if (!c) continue
    const t = Date.parse(c)
    if (!Number.isNaN(t) && t > Date.parse('1995-01-01') && t < Date.now() + 86_400_000) return new Date(t).toISOString()
  }
  return null
}

export function htmlTitle(html: string): string {
  const og = meta(html, ['og:title', 'twitter:title'])
  const t = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1]
  return decodeEntities((og || t || '').replace(/\s+/g, ' ').trim()).slice(0, 200)
}

// Removes elements whose text is never the article: scripts, styles, navigation, headers and footers,
// sidebars, forms, cookie banners and hidden blocks. Nested same-name tags are handled by repeating.
function stripBoilerplate(html: string): string {
  let s = html.replace(/<!--[\s\S]*?-->/g, ' ')
  for (const tag of ['script', 'style', 'noscript', 'svg', 'template', 'iframe', 'canvas', 'select', 'button']) {
    s = s.replace(new RegExp(`<${tag}\\b[\\s\\S]*?<\\/${tag}>`, 'gi'), ' ')
  }
  for (let i = 0; i < 3; i++) {
    for (const tag of ['nav', 'header', 'footer', 'aside', 'form', 'menu', 'dialog']) {
      s = s.replace(new RegExp(`<${tag}\\b[^>]*>(?:(?!<${tag}\\b)[\\s\\S])*?<\\/${tag}>`, 'gi'), ' ')
    }
  }
  s = s.replace(/<(div|section|ul|p)\b[^>]*(?:class|id)\s*=\s*["'][^"']*(cookie|consent|banner|newsletter|subscribe|share|social|related|comments?|advert|promo|breadcrumb|sidebar|popup|modal)[^"']*["'][^>]*>[\s\S]*?<\/\1>/gi, ' ')
  return s
}

function mainRegion(html: string): string {
  for (const re of [/<article\b[^>]*>([\s\S]*?)<\/article>/gi, /<main\b[^>]*>([\s\S]*?)<\/main>/gi]) {
    const parts = [...html.matchAll(re)].map(m => m[1])
    const best = parts.sort((a, b) => b.length - a.length)[0]
    if (best && best.replace(/<[^>]+>/g, '').trim().length > 400) return best
  }
  const body = html.match(/<body\b[^>]*>([\s\S]*)<\/body>/i)?.[1]
  return body || html
}

export function htmlToText(html: string): string {
  const region = mainRegion(stripBoilerplate(html))
  return decodeEntities(
    region
      .replace(/<(br|hr)\s*\/?>/gi, '\n')
      .replace(/<\/(p|div|section|li|tr|h[1-6]|blockquote|pre|table|dd|dt)>/gi, '\n')
      .replace(/<(td|th)\b[^>]*>/gi, ' | ')
      .replace(/<li\b[^>]*>/gi, '\n- ')
      .replace(/<[^>]+>/g, ' ')
  )
    .replace(/[ \t\f\v\u00a0]+/g, ' ')
    .replace(/ *\n */g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

// The passages of a page that best match the question, in page order, within a character budget.
export function selectPassages(text: string, query: string, budget = 2400): string {
  if (text.length <= budget) return text
  const paragraphs = text.split(/\n+/).map(p => p.trim()).filter(p => p.length > 30)
  // Paragraphs longer than ~600 characters are split so one long block doesn't take the whole budget.
  const chunks: string[] = []
  for (const p of paragraphs) {
    if (p.length <= 700) chunks.push(p)
    else for (let i = 0; i < p.length; i += 600) chunks.push(p.slice(i, i + 650))
  }
  const q = new Set(terms(query))
  const qg = trigrams(query)
  const scored = chunks.map((c, i) => {
    const t = terms(c)
    const hits = t.filter(x => q.has(x)).length
    const numeric = /\d/.test(c) ? 0.1 : 0
    return { i, c, s: hits / Math.sqrt(t.length + 4) + cosine(qg, trigrams(c)) * 0.8 + numeric + (i === 0 ? 0.15 : 0) }
  })
  const chosen: typeof scored = []
  let used = 0
  for (const x of [...scored].sort((a, b) => b.s - a.s)) {
    if (used + x.c.length > budget) continue
    chosen.push(x)
    used += x.c.length + 5
    if (used > budget * 0.95) break
  }
  return chosen
    .sort((a, b) => a.i - b.i)
    .map(x => x.c)
    .join('\n…\n')
}
