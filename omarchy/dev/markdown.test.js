// Unit tests for how replies are rendered in the island (omarchy/plugin/ui/markdown.js):
//   node --test omarchy/dev/markdown.test.js
const test = require("node:test")
const assert = require("node:assert")
const fs = require("node:fs")
const path = require("node:path")
const vm = require("node:vm")

const src = fs.readFileSync(path.join(__dirname, "../plugin/ui/markdown.js"), "utf8").replace(/^\.pragma library/m, "")
const ctx = { module: { exports: {} } }
vm.runInNewContext(src, ctx)
const M = ctx.module.exports
const html = (md) => M.toHtml(md, { faint: "#999", code: "#abc" })

const RESEARCH = `Recent updates from Almaty include holiday preparations and weather shifts [1].

### Key Headlines and Local News
- **Republic Day Weekend:** Kazakhstanis are preparing for a long weekend [1].
- **Weather Forecast:** Almaty is expected to see snow [1, 2].`

test("a research answer: heading, bullets, bold, citations as superscripts", () => {
  const h = html(RESEARCH)
  assert.ok(!/###|\*\*|\[1\]/.test(h), h)
  assert.ok(h.includes("<b>Key Headlines and Local News</b>"))
  assert.ok(h.includes("•&nbsp;&nbsp;<b>Republic Day Weekend:</b> Kazakhstanis"))
  assert.ok(h.includes('<font color="#999">¹</font>'))
  assert.ok(h.includes('<font color="#999">¹˒²</font>'))
  assert.ok(h.includes("shifts<font"), "the space before a citation goes")
  assert.ok(h.includes("<br><br><b>Key"), "a blank line before the heading")
})

test("text is escaped: a reply cannot inject markup", () => {
  const h = html('<img src="x"> & <b>not bold</b>')
  assert.ok(!h.includes("<img") && !h.includes("<b>not"))
  assert.ok(h.includes("&lt;img") && h.includes("&amp;"))
})

test("code spans and blocks are monospace and never formatted inside", () => {
  assert.ok(html("Run `a **b** [1]` now").includes('<font face="monospace" color="#abc">a **b** [1]</font>'))
  const h = html("Here:\n```bash\nls -la *.txt\n```\nDone.")
  assert.ok(h.includes('<font face="monospace" color="#abc">ls -la *.txt</font>'))
  assert.ok(!h.includes("```"))
})

test("links: Markdown links keep their label, bare links are shortened", () => {
  assert.ok(html("See [the docs](https://example.com/a?b=1&c=2).").includes('<a href="https://example.com/a?b=1&amp;c=2">the docs</a>'))
  assert.ok(html("Source: https://www.qazinform.com/news/x.").includes('<a href="https://www.qazinform.com/news/x">qazinform.com/news/x</a>.'))
})

test("numbered lists, quotes, rules and tables", () => {
  const h = html("1. One\n2. Two\n\n---\n> quoted\n\n| a | b |\n|---|---|\n| 1 | 2 |")
  assert.ok(h.includes("1.&nbsp;&nbsp;One<br>2.&nbsp;&nbsp;Two"))
  assert.ok(!h.includes("---"))
  assert.ok(h.includes("<i>quoted</i>"))
  assert.ok(h.includes("a · b<br>1 · 2"))
})

test("italic does not eat file names or snake_case", () => {
  assert.ok(html("an *important* note").includes("<i>important</i>"))
  assert.ok(!html("open my_file_name.txt").includes("<i>"))
  assert.ok(!html("2 * 3 * 4").includes("<i>"))
})

test("plain(): one clean line for previews", () => {
  assert.strictEqual(M.plain(RESEARCH),
    "Recent updates from Almaty include holiday preparations and weather shifts. Key Headlines and Local News " +
    "Republic Day Weekend: Kazakhstanis are preparing for a long weekend. Weather Forecast: Almaty is expected to see snow.")
  assert.strictEqual(M.plain("See [docs](https://x.io) and `ls`"), "See docs and ls")
})

test("looksRich(): short chat lines stay plain text", () => {
  assert.strictEqual(M.looksRich("Sure, opening Firefox."), false)
  assert.strictEqual(M.looksRich("The time is 10:30 (UTC+5)."), false)
  assert.strictEqual(M.looksRich(RESEARCH), true)
  assert.strictEqual(M.looksRich("Done, see https://x.io"), true)
})
