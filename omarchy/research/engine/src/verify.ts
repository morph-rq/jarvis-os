// Citation-to-claim checks. Each answer sentence that cites a source is compared with that source's
// text: the page itself when it was opened, otherwise only the search snippet (reported as such, never
// as proof). Numbers in a claim must appear in the source. Sources that state a different number for the
// same claim are reported as a conflict. Purely lexical: it catches unsupported and mis-attributed
// claims, it cannot judge paraphrases that share no words with the source.

import { createCitationPattern } from '@/lib/utils/citation'

import type { Page, SourceMeta } from './context'
import { canonicalUrl, nameTerms, numbersIn, quantities, terms, windowTerms } from './text'

export type ClaimCheck = {
  sentence: string
  labels: string[]
  status: 'verified' | 'snippet' | 'weak'
  support: number
  missingNumbers: string[]
}

export type Conflict = { claim: string; values: Record<string, string[]> }

export type Verification = {
  claims: ClaimCheck[]
  summary: {
    cited_sentences: number
    verified: number
    snippet_only: number
    weak: number
    uncited_numeric: number
    distinct_domains: number
    evidence: 'strong' | 'moderate' | 'weak' | 'none'
  }
  conflicts: Conflict[]
}

function sentences(markdown: string): string[] {
  return markdown
    .replace(/```[\s\S]*?```/g, ' ')
    .split(/\n+/)
    .flatMap(line => {
      const clean = line.replace(/^\s*(#{1,6}|[-*]|\d+\.)\s+/, '').trim()
      if (!clean || /^\|?\s*:?-{2,}/.test(clean)) return []
      // Citations follow the sentence's period, so a sentence ends after its citation group.
      return clean.split(/(?<=[.!?…](?:\s*\[\s*\d+\s*\]\(#[^)\s]+\))*)\s+(?=[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ0-9"«(])/u)
    })
    .map(s => s.trim())
    .filter(s => s.length > 12)
}

function evidenceFor(source: SourceMeta, pages: Map<string, Page>): { text: string; opened: boolean } {
  const page = pages.get(canonicalUrl(source.url))
  if (page && page.text.trim().length > 40) return { text: `${page.title}\n${page.text}`, opened: true }
  return { text: `${source.title}\n${source.snippet}`, opened: false }
}

const termCache = new WeakMap<object, { terms: Set<string>; numbers: Set<string>; text: string }>()

function indexed(key: object, text: string) {
  let e = termCache.get(key)
  if (!e || e.text !== text) {
    e = { terms: new Set(terms(text)), numbers: new Set(numbersIn(text)), text }
    termCache.set(key, e)
  }
  return e
}

export function verify(markdown: string, sources: Map<string, SourceMeta>, pages: Map<string, Page>): Verification {
  const claims: ClaimCheck[] = []
  let uncitedNumeric = 0
  const citedDomains = new Set<string>()
  for (const raw of sentences(markdown)) {
    const labels = [...raw.matchAll(createCitationPattern())].map(m => m[2]).filter(l => sources.has(l))
    const sentence = raw.replace(createCitationPattern(), '').replace(/\s+/g, ' ').trim()
    const claimTerms = [...new Set(terms(sentence))]
    const claimNumbers = numbersIn(sentence).filter(n => n.length > 1 || sentence.length < 80)
    if (!labels.length) {
      if (claimNumbers.length && claimTerms.length > 3) uncitedNumeric++
      continue
    }
    let best: ClaimCheck | null = null
    for (const label of labels) {
      const source = sources.get(label) as SourceMeta
      citedDomains.add(source.domain)
      const ev = evidenceFor(source, pages)
      const idx = indexed(source, ev.text)
      const support = claimTerms.length ? claimTerms.filter(t => idx.terms.has(t)).length / claimTerms.length : 0
      const missingNumbers = claimNumbers.filter(n => !idx.numbers.has(n))
      const ok = support >= 0.45 && missingNumbers.length === 0
      const status: ClaimCheck['status'] = ok ? (ev.opened ? 'verified' : 'snippet') : 'weak'
      const check = { sentence: sentence.slice(0, 240), labels, status, support: Math.round(support * 100) / 100, missingNumbers }
      const order = { verified: 2, snippet: 1, weak: 0 }
      if (!best || order[status] > order[best.status] || (status === best.status && support > best.support)) best = check
    }
    if (best) claims.push(best)
  }
  const verified = claims.filter(c => c.status === 'verified').length
  const snippetOnly = claims.filter(c => c.status === 'snippet').length
  const weak = claims.filter(c => c.status === 'weak').length
  const total = claims.length
  const evidence: Verification['summary']['evidence'] =
    total === 0
      ? 'none'
      : verified / total >= 0.6 && citedDomains.size >= 2
        ? 'strong'
        : (verified + snippetOnly) / total >= 0.6
          ? 'moderate'
          : 'weak'
  return {
    claims,
    summary: {
      cited_sentences: total,
      verified,
      snippet_only: snippetOnly,
      weak,
      uncited_numeric: uncitedNumeric,
      distinct_domains: citedDomains.size,
      evidence
    },
    conflicts: conflicts(claims, sources, pages)
  }
}

// A conflict: another source, writing about the same thing (most of the claim's words), gives a different
// value in the same unit ("1,200 people" vs "1,450 people"), and does not also give the claimed value.
function conflicts(claims: ClaimCheck[], sources: Map<string, SourceMeta>, pages: Map<string, Page>): Conflict[] {
  const out: Conflict[] = []
  const sourceSentences = [...sources.values()].map(s => {
    const ev = evidenceFor(s, pages)
    return {
      label: s.label,
      url: canonicalUrl(s.url),
      sentences: ev.text.split(/(?<=[.!?])\s+|\n+/).filter(x => /\d/.test(x)).slice(0, 400)
    }
  })
  for (const claim of claims) {
    const claimQs = quantities(claim.sentence)
    if (!claimQs.length) continue
    const citedUrls = new Set(claim.labels.map(l => canonicalUrl((sources.get(l) as SourceMeta).url)))
    for (const cq of claimQs) {
      const about = new Set(windowTerms(claim.sentence, cq.index))
      // The thing a claim is about may be named anywhere in its sentence ("The Vega Pro 14 costs … 16 GB").
      const names = new Set(nameTerms(claim.sentence, 0, claim.sentence.length))
      if (about.size < 3) continue
      const supporting = new Set<string>()
      const other: Record<string, Set<string>> = {}
      for (const src of sourceSentences) {
        for (const s of src.sentences) {
          for (const q of quantities(s)) {
            if (q.unit !== cq.unit) continue
            const near = windowTerms(s, q.index)
            const shared = near.filter(x => about.has(x)).length
            if (shared < 3 || shared / Math.min(about.size, new Set(near).size) < 0.4) continue
            // The same thing: most of the names near the claim's number are near this one too.
            if (names.size) {
              const nearNames = new Set(nameTerms(s, 0, s.length))
              if ([...names].filter(n => nearNames.has(n)).length / names.size < 0.5) continue
            }
            if (q.value === cq.value) supporting.add(src.label)
            else if (!citedUrls.has(src.url)) (other[q.value] ||= new Set()).add(src.label)
          }
        }
      }
      if (supporting.size && Object.keys(other).length) {
        out.push({
          claim: claim.sentence.slice(0, 200),
          values: Object.fromEntries([
            [`${cq.value} ${cq.unit}`, [...supporting].slice(0, 4)],
            ...Object.entries(other).slice(0, 3).map(([v, l]) => [`${v} ${cq.unit}`, [...l].slice(0, 4)])
          ])
        })
        break
      }
    }
    if (out.length >= 3) break
  }
  return out
}

// The specific things a question names: capitalized words after the first, acronyms, model names with
// digits ("Quokkatron 9000", "NVIDIA", "Orion-7"). Lowercase questions (often voice) name none.
export function namedInQuery(query: string): string[] {
  const words = query.match(/[\p{L}\p{N}][\p{L}\p{N}.+-]*/gu) || []
  const out: string[] = []
  words.forEach((w, i) => {
    const cap = /^[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ]/.test(w)
    const acronym = /^[A-ZА-ЯЁ]{3,}$/.test(w)
    const model = /\p{L}/u.test(w) && /\d/.test(w)
    const bigNumber = /^\d{3,}$/.test(w) && !/^(19|20)\d{2}$/.test(w)
    if ((cap && i > 0) || acronym || model || bigNumber) out.push(w.toLowerCase().replace(/[.+-]+$/, ''))
  })
  return [...new Set(out)].filter(w => w.length >= 3)
}

// Names from the question that no cited source mentions: the answer is probably about something else.
export function offTopic(query: string, cited: SourceMeta[], pages: Map<string, Page>): string[] {
  const names = namedInQuery(query)
  if (!names.length || !cited.length) return []
  const text = cited.map(s => `${s.title} ${s.snippet} ${pages.get(canonicalUrl(s.url))?.text || ''}`).join(' ').toLowerCase()
  const missing = names.filter(n => !text.includes(n))
  // All of them missing, or the most specific one (a model name or a number) missing.
  return missing.length === names.length || missing.some(n => /\d/.test(n)) ? missing : []
}
