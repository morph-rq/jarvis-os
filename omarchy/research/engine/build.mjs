// Builds the research engine: Jarvis's src/ plus Morphic's lib/ (from the pinned checkout) into one
// file, dist/engine.mjs. Morphic's source is used as is; three imports are redirected:
//   - Morphic's search provider factory -> src/providers.ts (adds Jarvis's SearXNG provider),
//   - next/headers -> a stub (the engine has no Next.js; that code path is never taken),
//   - exa-js -> a stub (a paid provider's client, not bundled).
//
//   MORPHIC_SRC=/path/to/morphic node build.mjs [--out dist/engine.mjs]

import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { build } from 'esbuild'

const here = dirname(fileURLToPath(import.meta.url))
const pkg = JSON.parse(readFileSync(join(here, 'package.json'), 'utf8'))
const morphic = resolve(process.env.MORPHIC_SRC || join(here, '../../../upstream/morphic'))
const outIdx = process.argv.indexOf('--out')
const outfile = resolve(outIdx > 0 ? process.argv[outIdx + 1] : join(here, 'dist/engine.mjs'))

if (!existsSync(join(morphic, 'lib/agents/researcher.ts'))) {
  console.error(`Morphic source not found at ${morphic} (set MORPHIC_SRC)`)
  process.exit(1)
}
// The checkout must be the pinned commit: the engine is only tested against it.
try {
  const head = execFileSync('git', ['-C', morphic, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim()
  if (head !== pkg.morphic.commit && !process.env.MORPHIC_ALLOW_OTHER_COMMIT) {
    console.error(`Morphic checkout is at ${head}, expected ${pkg.morphic.commit} (${pkg.morphic.tag})`)
    process.exit(1)
  }
} catch {
  if (!process.env.MORPHIC_ALLOW_OTHER_COMMIT) {
    console.error(`${morphic} is not a git checkout; cannot confirm it is Morphic ${pkg.morphic.tag}`)
    process.exit(1)
  }
}

const providersIndex = join(morphic, 'lib/tools/search/providers/index.ts')

const jarvisMorphic = {
  name: 'jarvis-morphic',
  setup(b) {
    b.onResolve({ filter: /^morphic-original:providers$/ }, () => ({ path: providersIndex }))
    b.onResolve({ filter: /^\.\/search\/providers$/ }, args =>
      args.importer === join(morphic, 'lib/tools/search.ts') ? { path: join(here, 'src/providers.ts') } : undefined
    )
    b.onResolve({ filter: /^next\/headers$/ }, () => ({ path: join(here, 'src/stubs/next-headers.ts') }))
    b.onResolve({ filter: /^exa-js$/ }, () => ({ path: join(here, 'src/stubs/exa.ts') }))
    b.onResolve({ filter: /^@\// }, async args => {
      const r = await b.resolve(`./${args.path.slice(2)}`, { resolveDir: morphic, kind: args.kind })
      return r.errors.length ? undefined : { path: r.path }
    })
  }
}

await build({
  entryPoints: [join(here, 'src/server.ts')],
  outfile,
  bundle: true,
  platform: 'node',
  format: 'esm',
  target: 'node22',
  sourcemap: 'linked',
  legalComments: 'linked',
  nodePaths: [join(here, 'node_modules')],
  plugins: [jarvisMorphic],
  jsx: 'automatic',
  logLevel: 'warning',
  define: { 'process.env.NODE_ENV': '"production"' },
  banner: { js: "import { createRequire as __jarvisRequire } from 'node:module'; const require = __jarvisRequire(import.meta.url);" }
})
// The pure functions again, for the unit tests (node --test test/).
if (!process.argv.includes('--no-lib')) {
  await build({
    entryPoints: [join(here, 'src/lib.ts')],
    outfile: join(dirname(outfile), 'lib.mjs'),
    bundle: true,
    platform: 'node',
    format: 'esm',
    target: 'node22',
    nodePaths: [join(here, 'node_modules')],
    plugins: [jarvisMorphic],
    jsx: 'automatic',
    logLevel: 'warning',
    banner: { js: "import { createRequire as __jarvisRequire } from 'node:module'; const require = __jarvisRequire(import.meta.url);" }
  })
}
console.log(`built ${outfile} from Morphic ${pkg.morphic.tag} (${pkg.morphic.commit.slice(0, 7)})`)
