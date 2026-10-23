// Per-request state, reachable from Morphic's tools without changing their signatures: the search
// provider and the fetch tool read the current request (mode, language, limits, abort signal) here.

import { AsyncLocalStorage } from 'node:async_hooks'

import type { Lang } from './text'

export type Mode = 'instant' | 'adaptive' | 'thorough'

export type SourceMeta = {
  label: string
  url: string
  title: string
  domain: string
  snippet: string
  published: string | null
  engines: string[]
  query: string
  cached: boolean
  retrievedAt: number
  opened: boolean
  rank: number
}

export type Page = { url: string; title: string; text: string; published: string | null; fetchedAt: number; cached: boolean }

export type Event = { t: string; [key: string]: unknown }

export type RequestCtx = {
  id: string
  mode: Mode
  lang: Lang
  fresh: boolean
  scope: string // "global", or the conversation's id for private queries
  query: string // the user's question, used to pick passages from pages
  signal: AbortSignal
  emit: (event: Event) => void
  limits: { searches: number; fetches: number; results: number; searchTimeoutMs: number; fetchTimeoutMs: number }
  counters: { searches: number; fetches: number; cacheHits: number; engineFailures: number; fallbacks: number; injectionFlags: number }
  sources: Map<string, SourceMeta> // by citation label
  pages: Map<string, Page> // by canonical URL
  searches: Map<string, unknown> // normalized query -> the provider's result (no repeated search in one request)
  timings: { searchMs: number[]; fetchMs: number[] }
  visible: string[] // the web text the model was shown (after sanitizing), for the answer's link check
}

export const requestStore = new AsyncLocalStorage<RequestCtx>()

export function current(): RequestCtx | undefined {
  return requestStore.getStore()
}
