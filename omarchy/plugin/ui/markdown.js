.pragma library
// Jarvis's replies are Markdown (the models write it, research answers cite sources as [1]). The island
// shows them as Qt StyledText: headings become bold lines, lists get bullets, **bold**, *italic*, `code`
// and links are rendered, citation numbers become small superscripts, and anything else is escaped, so
// a reply can never inject markup. plain() is the same text without any formatting, for one-line previews.
// omarchy/dev/markdown.test.js runs both.

var SUP = { "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹", ",": "˒" }
// [1], [1, 2], [1][2] (not a link's [text](url))
var CITE = /\s?\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\](?!\()/g

function escape(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;")
}

function sup(nums) {
  return nums.replace(/\s/g, "").split("").map(function(c) { return SUP[c] || c }).join("")
}

// Inline formatting of one escaped line. Code spans are cut out first so nothing inside them is formatted.
function inline(text, o) {
  var codes = []
  var s = escape(text).replace(/`([^`]+)`/g, function(m, c) {
    codes.push(c)
    return "\u0000" + (codes.length - 1) + "\u0000"
  })
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, function(m, label, url) {
    return '<a href="' + url + '">' + label + "</a>"
  })
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?])/g, function(m, pre, url) {
    var shown = url.replace(/^https?:\/\/(www\.)?/, "")
    return pre + '<a href="' + url + '">' + (shown.length > 40 ? shown.slice(0, 38) + "…" : shown) + "</a>"
  })
  s = s.replace(/\*\*([^*]+)\*\*|__([^_]+)__/g, function(m, a, b) { return "<b>" + (a || b) + "</b>" })
  s = s.replace(/(^|[^\w*])\*([^*\s][^*]*?)\*(?!\w)|(^|[^\w_])_([^_\s][^_]*?)_(?!\w)/g, function(m, p1, a, p2, b) {
    return (p1 !== undefined ? p1 : p2) + "<i>" + (a || b) + "</i>"
  })
  s = s.replace(CITE, function(m, nums) {
    return '<font color="' + o.faint + '">' + sup(nums) + "</font>"
  })
  return s.replace(/\u0000(\d+)\u0000/g, function(m, i) {
    return '<font face="monospace" color="' + o.code + '">' + codes[+i] + "</font>"
  })
}

// Markdown → StyledText. o: {faint, code, heading} colours (any CSS colour string).
function toHtml(md, o) {
  o = o || {}
  o.faint = o.faint || "#8a8a8a"
  o.code = o.code || o.faint
  var lines = String(md || "").replace(/\r/g, "").split("\n")
  var out = [], fence = false, code = [], gap = false
  function push(html) {
    out.push((out.length ? (gap ? "<br><br>" : "<br>") : "") + html)
    gap = false
  }
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i]
    if (/^\s*(```|~~~)/.test(line)) {
      if (fence) {
        push('<font face="monospace" color="' + o.code + '">' + code.map(escape).join("<br>").replace(/ {2}/g, "&nbsp;&nbsp;") + "</font>")
        code = []
      }
      fence = !fence
      continue
    }
    if (fence) { code.push(line); continue }
    var t = line.trim()
    if (!t) { if (out.length) gap = true; continue }
    if (/^([-*_])(\s*\1){2,}$/.test(t)) continue                       // --- rules
    if (/^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/.test(t)) continue  // table separators
    var m
    if ((m = t.match(/^#{1,6}\s+(.*?)\s*#*$/))) {
      if (out.length) gap = true
      push("<b>" + inline(m[1], o) + "</b>")
      continue
    }
    if (/^\|.*\|$/.test(t)) {
      push(t.slice(1, -1).split("|").map(function(c) { return inline(c.trim(), o) }).join(" · "))
      continue
    }
    if ((m = line.match(/^(\s*)[-*+•]\s+(.*)$/))) {
      push((m[1].length >= 2 ? "&nbsp;&nbsp;&nbsp;&nbsp;◦ " : "•&nbsp;&nbsp;") + inline(m[2], o))
      continue
    }
    if ((m = line.match(/^(\s*)(\d{1,3})[.)]\s+(.*)$/))) {
      push((m[1].length >= 2 ? "&nbsp;&nbsp;&nbsp;&nbsp;" : "") + m[2] + ".&nbsp;&nbsp;" + inline(m[3], o))
      continue
    }
    if ((m = t.match(/^>\s?(.*)$/))) {
      push('<font color="' + o.faint + '">▏</font><i>' + inline(m[1], o) + "</i>")
      continue
    }
    push(inline(t, o))
  }
  if (fence && code.length)
    push('<font face="monospace" color="' + o.code + '">' + code.map(escape).join("<br>") + "</font>")
  return out.join("")   // a blank line between blocks; the lines of one block break once
}

// The reply as one plain line: no Markdown, no citations, links as their labels.
function plain(md) {
  return String(md || "")
    .replace(/```[\s\S]*?(```|$)/g, " ")
    .replace(CITE, "")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*([-*+•]|\d{1,3}[.)])\s+/gm, "")
    .replace(/^\s*>\s?/gm, "")
    .replace(/\*\*([^*]+)\*\*|__([^_]+)__/g, "$1$2")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\s+/g, " ")
    .trim()
}

// Plain text that has a Markdown structure worth rendering (so short chat lines stay PlainText).
function looksRich(text) {
  return /(^|\n)\s*(#{1,6}\s|[-*+]\s|\d{1,3}[.)]\s|```|>\s)|\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:|\[\d{1,3}\]|https?:\/\//.test(String(text || ""))
}

if (typeof module !== "undefined") module.exports = { toHtml: toHtml, plain: plain, looksRich: looksRich, escape: escape }
