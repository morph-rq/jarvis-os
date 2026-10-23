// Web content is data. Two cheap, deterministic layers in front of the model's own instructions:
// sentences addressed to an AI (classic prompt injection) are removed from what the model reads, and
// links or e-mail addresses in the answer that no source contains are removed from what the user sees.

const INJECTION = [
  /\bignore (all |any )?(of )?(the |your )?(previous|prior|above|earlier) (instructions|prompts?|rules|messages)/i,
  /\b(note|message|instructions?|attention)\s+(to|for)\s+(all\s+)?(ai|llms?|language models?|assistants?|chatbots?|ai assistants?)\b/i,
  /\bdisregard (all |any )?(your|the|previous) (instructions|rules|guidelines)/i,
  /\b(reveal|print|show) (your|the) (system prompt|instructions|hidden prompt)/i,
  /\byou are now (in )?(developer|dan|jailbreak) mode\b/i,
  /\b(send|forward|e-?mail|post) (me |us )?(your|the user'?s?) (api ?keys?|passwords?|tokens?|credentials|secrets?)\b/i,
  /игнорируй(те)? (все )?(предыдущие|прошлые) (инструкции|указания)/i,
  /(указание|сообщение|инструкция) для (ии|ai|ассистент\w*|нейросет\w*)/i
]

export function isInjection(sentence: string): boolean {
  return INJECTION.some(re => re.test(sentence))
}

export function sanitizeUntrusted(text: string): { text: string; flagged: number } {
  let flagged = 0
  const out = text
    .split(/(?<=[.!?])\s+|\n/)
    .map(s => {
      if (isInjection(s)) {
        flagged++
        return '[removed: text addressed to AI assistants]'
      }
      return s
    })
    .join(' ')
  return { text: flagged ? out : text, flagged }
}

const URL_RE = /\bhttps?:\/\/[^\s)\]>"']+/gi
const EMAIL_RE = /\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b/g

// Removes addresses the sources don't contain. Citation links are added after this runs.
export function guardLinks(answer: string, allowedText: string): { text: string; removed: string[] } {
  const removed: string[] = []
  const allowed = allowedText.toLowerCase()
  let text = answer.replace(/\[([^\]]*)\]\((https?:\/\/[^)\s]+)\)/g, (m, label: string, url: string) => {
    if (allowed.includes(url.toLowerCase())) return m
    removed.push(url)
    return label
  })
  text = text.replace(URL_RE, match => {
    const url = match.replace(/[.,;:!?]+$/, '')
    const tail = match.slice(url.length)
    if (allowed.includes(url.toLowerCase())) return match
    removed.push(url)
    return `[link removed]${tail}`
  })
  text = text.replace(EMAIL_RE, email => {
    if (allowed.includes(email.toLowerCase())) return email
    removed.push(email)
    return '[address removed]'
  })
  return { text, removed }
}
