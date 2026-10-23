// Exa is a paid search API; the engine bundle leaves its client out. Choosing it fails clearly.
export default class Exa {
  constructor() {
    throw new Error('The Exa search provider is not included in Jarvis\'s research engine; use SearXNG.')
  }
}
