// Text helpers shared by ranking, caching and verification: normalization, language, tokens.
// English, Russian and Kazakh are first-class; everything else falls back to plain tokenization.

const KAZAKH_LETTERS = /[әғқңөұүһі]/i
const CYRILLIC = /[а-яё]/i

export type Lang = 'en' | 'ru' | 'kk' | 'other'

export function detectLang(text: string): Lang {
  if (KAZAKH_LETTERS.test(text)) return 'kk'
  if (CYRILLIC.test(text)) return 'ru'
  if (/[a-z]/i.test(text)) return 'en'
  return 'other'
}

// What people say to Jarvis before the actual question; never worth sending to a search engine.
const LEADING_FILLER = [
  /^(hey |ok |okay )?jarvis[,!.:]?\s*/i,
  /^(джарвис|жарвис)[,!.:]?\s*/i,
  /^(can|could|would) you (please )?(tell me|find( out)?|look up|search( for)?|check)\s+/i,
  /^(please )?(tell me|find( out)?|look up|search( for)?|google|research)\s+/i,
  /^(i (want|need) to know|do you know)\s+/i,
  /^(скажи|подскажи|расскажи|найди|поищи|узнай)(,| мне| пожалуйста)*\s+/i,
  /^(маған )?(айтшы|тауып бер|іздеші)\s+/i
]

export function normalizeQuery(query: string): string {
  let q = query.normalize('NFKC').replace(/\s+/g, ' ').trim()
  for (let i = 0; i < 3; i++) {
    const before = q
    for (const re of LEADING_FILLER) q = q.replace(re, '')
    if (q === before) break
  }
  q = q.replace(/[?!.]+$/, '').trim()
  return q || query.trim()
}

export function cacheKeyText(query: string): string {
  return normalizeQuery(query)
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s.+#-]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

const STOP = new Set(
  (
    'a an and are as at be by can could did do does for from has have how i in is it its me my of on or ' +
    'please should tell than that the their there these this to vs was what when where which who why will ' +
    'with would you your about between best latest new newest current today now ' +
    'и в во на не что как это по с со за из к ко о об от до для ли же а но или у мне мой моя мои ' +
    'какой какая какие каков кто где когда почему ли самый самая самые сейчас сегодня новый новая ' +
    'және мен бұл не қалай қандай ма ме ба бе па пе да де та те ең'
  ).split(' ')
)

export function tokens(text: string): string[] {
  return (text.toLowerCase().normalize('NFKC').match(/[\p{L}\p{N}][\p{L}\p{N}.+#-]*/gu) || [])
    .map(t => t.replace(/[.-]+$/, ''))
    .filter(t => t.length > 0)
}

// A crude, language-agnostic stem: inflected Russian and Kazakh words share their first letters, and
// a 6-letter prefix is enough to match "видеокарта/видеокарты" or "model/models" without a dictionary.
export function stem(token: string): string {
  if (/^\d/.test(token)) return token
  return token.length > 6 ? token.slice(0, 6) : token.replace(/(es|s)$/, '')
}

export function terms(text: string): string[] {
  return tokens(text)
    .filter(t => !STOP.has(t) && (t.length > 1 || /\d/.test(t)))
    .map(stem)
}

// Numbers as written: "8,849" and "512 000" are one number each; "29, 2026" is two.
export function numbersIn(text: string): string[] {
  return (text.match(/\d{1,3}(?:[,\u00a0\u202f ]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?/g) || []).map(n =>
    n.replace(/[,\u00a0\u202f ]/g, '')
  )
}

export type Quantity = { value: string; unit: string; index: number }

const CURRENCY: Record<string, string> = { $: 'usd', '€': 'eur', '£': 'gbp', '₸': 'kzt', '₽': 'rub' }
// Units worth comparing across sources, by their first letters (English, Russian, Kazakh).
const UNITS: Array<[RegExp, string]> = [
  [/^(people|persons?|employees?|staff|workers|человек|сотрудник|работник|адам|қызметкер)/, 'people'],
  [/^(users?|customers?|subscribers?|пользовател|клиент)/, 'users'],
  [/^(usd|dollars?|доллар)/, 'usd'], [/^(eur|euros?|евро)/, 'eur'], [/^(tenge|тенге|теңге)/, 'kzt'], [/^(rub|рубл)/, 'rub'],
  [/^(tb|тб)$/, 'tb'], [/^(gb|гб)$/, 'gb'], [/^(mb|мб)$/, 'mb'], [/^(kg|кг)$/, 'kg'], [/^(km|км)$/, 'km'], [/^(m|м|metres?|meters?|метр)/, 'm'],
  [/^(hours?|hrs?|h|час|сағат)/, 'hours'], [/^(minutes?|mins?|минут)/, 'minutes'], [/^(days?|дн|день|дней|күн)/, 'days'],
  [/^(tokens?|токен)/, 'tokens'], [/^(cores?|ядер|ядра)/, 'cores'], [/^(w|watts?|вт)$/, 'w'], [/^mah$/, 'mah'],
  [/^(inch|inches|дюйм)/, 'inch'], [/^(%|percent|процент|пайыз)/, '%'], [/^(km\/h|mph|км\/ч)/, 'speed'],
  [/^(feet|ft|фут)/, 'ft'], [/^(fps)$/, 'fps'], [/^(ghz|ггц)$/, 'ghz']
]

// Numbers with what they count: "$949" (usd), "1,200 people", "512 GB", "1997" (year). Numbers followed by
// other words ("Pro 14 costs") are not quantities and are never compared.
export function quantities(text: string): Quantity[] {
  const out: Quantity[] = []
  const re = /([$€£₸₽])?\s?(\d{1,3}(?:[,\u00a0\u202f ]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?:[\s-]?(%|[a-zа-яёәғқңөұүһі/]+))?/giu
  for (const m of text.matchAll(re)) {
    const value = m[2].replace(/[,\u00a0\u202f ]/g, '')
    let unit = m[1] ? CURRENCY[m[1]] : ''
    if (!unit && m[3]) unit = UNITS.find(([u]) => u.test(m[3].toLowerCase()))?.[1] || ''
    if (!unit && /^(19|20)\d{2}$/.test(value)) unit = 'year'
    if (!unit) continue
    out.push({ value, unit, index: m.index || 0 })
  }
  return out
}

// Names near a position (capitalized words, model names like X1): which thing a number belongs to.
const NOT_NAMES = new Set(['the', 'a', 'an', 'it', 'its', 'this', 'that', 'in', 'on', 'at', 'by', 'for', 'and', 'our', 'we',
  'they', 'he', 'she', 'according', 'в', 'на', 'по', 'и', 'это', 'он', 'она', 'они'])
export function nameTerms(text: string, index: number, span = 48): string[] {
  const window = text.slice(Math.max(0, index - span), index + span)
  return (window.match(/[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][\p{L}\d-]*|\b[A-Za-z]+\d+\b/gu) || [])
    // Short acronyms (GB, RAM, SSD, USD) describe the number, not the thing.
    .filter(w => !/^[A-ZА-ЯЁ]{1,4}$/.test(w))
    .map(w => w.toLowerCase())
    .filter(w => !NOT_NAMES.has(w))
    .map(stem)
}

// The words around a position: what a number is about.
export function windowTerms(text: string, index: number, span = 48): string[] {
  return terms(text.slice(Math.max(0, index - span), index + span).replace(/\d[\d,.\s]*/g, ' '))
}

export function trigrams(text: string): Map<string, number> {
  const t = ` ${tokens(text).join(' ')} `
  const out = new Map<string, number>()
  for (let i = 0; i + 3 <= t.length; i++) {
    const g = t.slice(i, i + 3)
    out.set(g, (out.get(g) || 0) + 1)
  }
  return out
}

export function cosine(a: Map<string, number>, b: Map<string, number>): number {
  let dot = 0
  let na = 0
  let nb = 0
  for (const [, v] of a) na += v * v
  for (const [k, v] of b) {
    nb += v * v
    const av = a.get(k)
    if (av) dot += av * v
  }
  return na && nb ? dot / Math.sqrt(na * nb) : 0
}

// Questions whose answer changes over time: they get fresh results and a short cache life.
const FRESH = [
  /\b(latest|newest|recent(ly)?|current(ly)?|today|tonight|yesterday|this (week|month|year)|now|right now|breaking|news|update[sd]?|announced?|released?|price|stock|score|weather|live)\b/i,
  /\b20[2-3]\d\b/,
  /(последн|новейш|свеж|сегодня|вчера|сейчас|текущ|новост|цена|курс|вышел|вышла|анонс)/i,
  /(соңғы|жаңа|бүгін|кеше|қазір|жаңалық|баға)/i
]

export function isTimeSensitive(query: string): boolean {
  return FRESH.some(re => re.test(query))
}

const TECHNICAL = [
  /\b(api|sdk|cli|install|config(ure|uration)?|error|exception|traceback|function|library|package|version|docs?|documentation|kernel|driver|linux|arch|hyprland|python|node|rust|go|java(script)?|typescript|docker|systemd|bash|git|npm|pip|cuda|gpu|compile|build|deprecat\w*)\b/i,
  /\b\w+\(\)|--\w+|\w+\.\w+\(/,
  /(ошибк|установ|настро|драйвер|библиотек|документац)/i
]

export function isTechnical(query: string): boolean {
  return TECHNICAL.some(re => re.test(query))
}

// Private details that must not reach a search engine or a shared cache.
const SENSITIVE = [
  /[\w.+-]+@[\w-]+\.[\w.]+/, // e-mail address
  /\+?\d[\d\s().-]{8,}\d/, // phone number
  /\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b/, // card number
  /\b(my|our) (password|passport|ssn|diagnosis|salary|bank|account|address|doctor|lawyer)\b/i,
  /\b(мой|моя|мои|мой) (пароль|паспорт|диагноз|зарплат|счёт|счет|адрес)/i
]

export function isSensitive(query: string): boolean {
  return SENSITIVE.some(re => re.test(query))
}

// Removes what a search engine never needs: e-mail addresses and phone numbers are replaced, not sent.
export function redactForSearch(query: string): string {
  return query
    .replace(/[\w.+-]+@[\w-]+\.[\w.]+/g, '')
    .replace(/\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b/g, '')
    .replace(/\+?\d[\d\s().-]{8,}\d/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '').toLowerCase()
  } catch {
    return ''
  }
}

// Same page, different address: tracking parameters, fragments, "www." and mobile hosts, trailing slashes.
export function canonicalUrl(url: string): string {
  try {
    const u = new URL(url)
    u.hash = ''
    u.hostname = u.hostname.toLowerCase().replace(/^(www|m|mobile|amp)\./, '')
    for (const key of [...u.searchParams.keys()]) {
      if (/^(utm_|fbclid|gclid|yclid|mc_|ref$|ref_src|source$|spm$|_ga)/i.test(key)) u.searchParams.delete(key)
    }
    u.searchParams.sort()
    if (u.pathname.length > 1 && u.pathname.endsWith('/')) u.pathname = u.pathname.replace(/\/+$/, '')
    return u.toString().replace(/^http:/, 'https:')
  } catch {
    return url
  }
}

export function jaccard(a: string[], b: string[]): number {
  const A = new Set(a)
  const B = new Set(b)
  if (!A.size || !B.size) return 0
  let inter = 0
  for (const x of A) if (B.has(x)) inter++
  return inter / (A.size + B.size - inter)
}

const MONTHS: Record<string, number> = {
  jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, sept: 8, oct: 9, nov: 10, dec: 11,
  янв: 0, фев: 1, мар: 2, апр: 3, мая: 4, май: 4, июн: 5, июл: 6, авг: 7, сен: 8, окт: 9, ноя: 10, дек: 11
}

// The date many engines put at the start of a snippet: "Oct 21, 2026 — …", "21 окт. 2026 г. …",
// "2026-10-21 …", "3 days ago · …", "5 часов назад …". Returns an ISO date, or null.
export function snippetDate(snippet: string, now = Date.now()): string | null {
  const head = snippet.slice(0, 40).toLowerCase()
  let m = head.match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (m) return iso(Date.UTC(+m[1], +m[2] - 1, +m[3]), now)
  m = head.match(/^([a-zа-яё]{3,5})\.?\s+(\d{1,2}),?\s+(\d{4})/)
  if (m && MONTHS[m[1].slice(0, 4)] !== undefined) return iso(Date.UTC(+m[3], MONTHS[m[1].slice(0, 4)], +m[2]), now)
  if (m && MONTHS[m[1].slice(0, 3)] !== undefined) return iso(Date.UTC(+m[3], MONTHS[m[1].slice(0, 3)], +m[2]), now)
  m = head.match(/^(\d{1,2})\s+([a-zа-яё]{3,8})\.?\s+(\d{4})/)
  if (m && MONTHS[m[2].slice(0, 3)] !== undefined) return iso(Date.UTC(+m[3], MONTHS[m[2].slice(0, 3)], +m[1]), now)
  m = head.match(/^(\d{1,2})\s+(minute|hour|day|week|мин|час|дн|день|дня|недел)\w*\s+(ago|назад)/)
  if (m) {
    const unit = m[2].startsWith('min') || m[2].startsWith('мин') ? 60e3 : m[2].startsWith('h') || m[2].startsWith('час') ? 3600e3 : m[2].startsWith('w') || m[2].startsWith('нед') ? 7 * 86400e3 : 86400e3
    return iso(now - +m[1] * unit, now)
  }
  return null
}

function iso(t: number, now: number): string | null {
  return Number.isFinite(t) && t <= now + 86_400_000 && t > Date.UTC(1995, 0, 1) ? new Date(t).toISOString() : null
}
