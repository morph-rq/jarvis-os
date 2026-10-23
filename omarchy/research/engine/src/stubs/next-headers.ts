// Morphic's lib/utils/url.ts imports next/headers to build its own base URL for the Next.js advanced
// search route. The engine has no Next.js and never takes that path (searches go to Jarvis's provider).
export async function headers(): Promise<Map<string, string>> {
  return new Map()
}
