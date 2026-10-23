// From the model's Markdown to what Jarvis shows and says: citations resolved to real sources (an
// unknown label is dropped, never guessed), Morphic's generative-UI spec blocks validated against
// Morphic's component catalog and turned into a short list of known block types, plus tables and
// timelines parsed out for native rendering and for other tools (spreadsheets, documents).

import { parseSpecBlock } from '@/lib/render/parse-spec-block'
import { stripSpecBlocks } from '@/lib/render/strip-spec-blocks'
import { createCitationPattern } from '@/lib/utils/citation'

import type { SourceMeta } from './context'
import { canonicalUrl } from './text'

export type Citation = { n: number; label: string; url: string; title: string; domain: string }

// One number per page: a page found by a search and then opened has two labels, but it is one source.
export function resolveCitations(text: string, sources: Map<string, SourceMeta>) {
  const order = new Map<string, number>() // canonical URL -> number
  const labelsByNumber = new Map<number, string>()
  const unknown: string[] = []
  const toNumber = (label: string) => {
    const key = canonicalUrl((sources.get(label) as SourceMeta).url)
    if (!order.has(key)) {
      order.set(key, order.size + 1)
      labelsByNumber.set(order.size, label)
    }
    return order.get(key) as number
  }
  const md = text.replace(createCitationPattern(), (_m, _num, label: string) => {
    const source = sources.get(label)
    if (!source) {
      unknown.push(label)
      return ''
    }
    return ` [${toNumber(label)}](${encodeURI(source.url)})`
  })
  const display = md.replace(/ \[(\d+)\]\([^)]*\)/g, ' [$1]')
  const citations: Citation[] = [...labelsByNumber.entries()].map(([n, label]) => {
    const s = sources.get(label) as SourceMeta
    return { n, label, url: s.url, title: s.title, domain: s.domain }
  })
  return { markdown: tidy(md), display: tidy(display), citations, unknown }
}

function tidy(s: string): string {
  return s
    .replace(/[ \t]+\n/g, '\n')
    .replace(/[ \t]{2,}\[(\d+)\]/g, ' [$1]')
    .replace(/(\[\d+\])(?:\s*\1)+/g, '$1')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

export type Block =
  | { type: 'images'; items: Array<{ src: string; sourceUrl?: string; title?: string; description?: string }> }
  | { type: 'related'; questions: string[] }
  | { type: 'table'; headers: string[]; rows: string[][] }
  | { type: 'timeline'; items: Array<{ date: string; text: string }> }

type SpecElement = { type: string; props?: Record<string, unknown>; children?: string[]; on?: Record<string, { action?: string; params?: Record<string, unknown> }> }

// Only what the catalog allows, and image URLs only when a search actually returned them.
export function specBlocks(text: string, knownImages: Set<string>): { blocks: Block[]; rejected: number } {
  const blocks: Block[] = []
  let rejected = 0
  for (const m of text.matchAll(/```spec\s*\n([\s\S]*?)```/g)) {
    let spec: { elements?: Record<string, SpecElement> }
    try {
      spec = parseSpecBlock(m[1]) as never
    } catch {
      rejected++
      continue
    }
    const elements = Object.values(spec.elements || {})
    const images = elements
      .filter(e => e.type === 'Image' && typeof e.props?.src === 'string')
      .map(e => ({
        src: e.props?.src as string,
        sourceUrl: typeof e.props?.sourceUrl === 'string' ? (e.props.sourceUrl as string) : undefined,
        title: typeof e.props?.title === 'string' ? (e.props.title as string) : undefined,
        description: typeof e.props?.description === 'string' ? (e.props.description as string) : undefined
      }))
    const verified = images.filter(i => knownImages.has(i.src) && /^https:\/\//.test(i.src))
    rejected += images.length - verified.length
    if (verified.length) blocks.push({ type: 'images', items: verified.slice(0, 4) })
    const questions = elements
      .filter(e => e.type === 'Button' && e.on?.press?.action === 'submitQuery')
      .map(e => String(e.on?.press?.params?.query || e.props?.text || '').trim())
      .filter(q => q.length > 0 && q.length < 160)
    if (questions.length) blocks.push({ type: 'related', questions: questions.slice(0, 3) })
  }
  return { blocks, rejected }
}

export function withoutSpec(text: string): string {
  // An unterminated block at the end of a cut-off answer is removed too.
  return stripSpecBlocks(text).replace(/```spec[\s\S]*$/, '').trim()
}

export function tables(markdown: string): Array<{ headers: string[]; rows: string[][] }> {
  const out: Array<{ headers: string[]; rows: string[][] }> = []
  const lines = markdown.split('\n')
  const cells = (line: string) =>
    line
      .trim()
      .replace(/^\||\|$/g, '')
      .split('|')
      .map(c => c.trim().replace(/\*\*/g, ''))
  for (let i = 0; i + 1 < lines.length; i++) {
    if (!/^\s*\|.*\|\s*$/.test(lines[i]) || !/^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) continue
    const headers = cells(lines[i])
    const rows: string[][] = []
    let j = i + 2
    for (; j < lines.length && /^\s*\|.*\|\s*$/.test(lines[j]); j++) rows.push(cells(lines[j]).slice(0, headers.length))
    if (headers.length >= 2 && rows.length) out.push({ headers, rows })
    i = j
  }
  return out
}

const DATE_LEAD = /^\s*[-*]\s+(\*\*)?((?:\d{1,2}\s+)?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}|\d{4}-\d{2}(?:-\d{2})?|(?:19|20)\d{2}(?:s)?|Q[1-4]\s+\d{4})(\*\*)?\s*[:—–-]\s*(.+)$/i

export function timeline(markdown: string): Array<{ date: string; text: string }> {
  const items = markdown
    .split('\n')
    .map(l => l.match(DATE_LEAD))
    .filter((m): m is RegExpMatchArray => m !== null)
    .map(m => ({ date: m[2], text: m[4].trim() }))
  return items.length >= 3 ? items.slice(0, 12) : []
}

// What Jarvis can say aloud: the first real paragraph, without headings, Markdown or citation marks.
export function spokenSummary(display: string, limit = 420): string {
  const paragraphs = display
    .split(/\n\s*\n/)
    .map(p => p.trim())
    .filter(p => p && !/^#{1,6}\s/.test(p) && !/^\|/.test(p))
  let text = (paragraphs[0] || '')
    .replace(/^[-*]\s+/gm, '')
    .replace(/\*\*|__|`/g, '')
    .replace(/\s*\[\d+\]/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
  if (text.length > limit) {
    const cut = text.slice(0, limit)
    const end = Math.max(cut.lastIndexOf('. '), cut.lastIndexOf('! '), cut.lastIndexOf('? '))
    text = end > 80 ? cut.slice(0, end + 1) : `${cut.trimEnd()}…`
  }
  return text
}
