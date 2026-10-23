// Stands in for Morphic's lib/tools/search/providers/index.ts inside the engine bundle (build.mjs
// redirects that one import). Everything Morphic exports is passed through; only "searxng" resolves to
// Jarvis's provider. A paid provider (tavily, exa, brave, firecrawl) still works when SEARCH_API names it
// and its key is set; nothing switches to one by itself.

import {
  createSearchProvider as morphicCreateSearchProvider,
  type SearchProvider,
  type SearchProviderType
} from 'morphic-original:providers'

import { JarvisSearxngProvider } from './searxng'

export * from 'morphic-original:providers'

let shared: JarvisSearxngProvider | undefined

export function createSearchProvider(type?: SearchProviderType): SearchProvider {
  const providerType = type || (process.env.SEARCH_API as SearchProviderType) || 'searxng'
  if (providerType === 'searxng') {
    shared ??= new JarvisSearxngProvider()
    return shared
  }
  return morphicCreateSearchProvider(providerType)
}
