// Build Coucou's character engine (windows/src/mochi/engine.ts) for the Jarvis island, from a local
// Coucou checkout at the pin in pins.env. The result, omarchy/plugin/ui/coucou/engine.js, stays on this
// machine (it is git-ignored): Coucou's code is MIT, but the Mochi character, its name and its sounds
// are reserved by its author (LICENSE-ASSETS.md), so Jarvis never redistributes them. Sounds and outfits
// are left out; everything the character draws and how it moves is Coucou's own engine.
//
//   node omarchy/coucou/build.mjs [COUCOU_CHECKOUT] [--any-revision]
import { build } from "esbuild"
import { execFileSync } from "node:child_process"
import { readFileSync, writeFileSync, mkdirSync } from "node:fs"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const here = dirname(fileURLToPath(import.meta.url))
const repo = resolve(here, "../..")
const args = process.argv.slice(2)
const src = resolve(args.find((a) => !a.startsWith("--")) || join(repo, "upstream/coucou"))
const pin = /COUCOU_COMMIT=(\w+)/.exec(readFileSync(join(here, "pins.env"), "utf8"))[1]
const rev = execFileSync("git", ["-C", src, "rev-parse", "HEAD"]).toString().trim()
if (rev !== pin && !args.includes("--any-revision")) {
  console.error(`coucou: ${src} is at ${rev}, Jarvis is pinned to ${pin} (or pass --any-revision)`)
  process.exit(1)
}
const stubs = {
  "core/sound": "export const Sound = { play() {}, setEnabled() {}, setVolume() {} };",
  "mochi/outfits": "export const PUMPKIN_BODY = ['#FFA94D', '#E8590C']; export function makeHead() { return null }\n" +
                   "export function drawOutfitBehind() {} export function drawOutfitFront() {}",
  "core/fonts": "export const SCRIPT_FONTS = '';",
}
const out = await build({
  entryPoints: [join(src, "windows/src/mochi/engine.ts")],
  bundle: true, write: false, format: "iife", globalName: "Coucou", target: "es2017", charset: "utf8",
  plugins: [{
    name: "jarvis-stubs",
    setup(b) {
      b.onResolve({ filter: /.*/ }, (a) => {
        for (const k of Object.keys(stubs)) if (a.path.endsWith(k) || a.path.endsWith(k.split("/")[1])) {
          if (a.path.includes(k.split("/")[1])) return { path: k, namespace: "stub" }
        }
        return undefined
      })
      b.onLoad({ filter: /.*/, namespace: "stub" }, (a) => ({ contents: stubs[a.path], loader: "ts" }))
    },
  }],
})
const dest = join(repo, "omarchy/plugin/ui/coucou/engine.js")
mkdirSync(dirname(dest), { recursive: true })
writeFileSync(dest, ".pragma library\n// Built locally by omarchy/coucou/build.mjs from Coucou " + rev +
  " (MIT code; the Mochi character is (c) Louis Raillé, personal use only, see LICENSE-ASSETS.md). Do not commit.\n" +
  readFileSync(join(here, "prelude.js"), "utf8") + "\n" + out.outputFiles[0].text)
console.log(`coucou: built ${dest} from ${rev}`)
