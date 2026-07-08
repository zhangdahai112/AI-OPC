/* ─── Utility functions ─── */

export function esc(s: unknown): string {
  return String(s == null ? "" : s).replace(
    /[&<>"]/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] || c
  );
}

export function fmt(n: number | string): string {
  n = +n || 0;
  return n >= 1000
    ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k"
    : String(n);
}

// role keys + Chinese names that can be @-mentioned
const MENTION_NAMES = [
  "coordinator", "analyst", "developer", "tester", "devops", "reporter",
  "项目经理", "需求分析", "开发", "测试", "运维", "上报",
];
const MENTION_RE = new RegExp(`@(${MENTION_NAMES.join("|")})`, "g");

/* ─── Inline markdown (applied to already-escaped text segments) ───
   Handles: links, bold, italic, strikethrough, @mentions. */
function inline(t: string): string {
  // links: [text](http…)  — url is already escaped so &amp; etc. stay safe
  t = t.replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
  );
  // bare autolinks
  t = t.replace(
    /(^|[\s(])(https?:\/\/[^\s<)]+)/g,
    '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>'
  );
  // bold
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  // italic (avoid eating word-internal underscores / bullet stars)
  t = t.replace(/(^|[^*])\*(?!\s)([^*\n]+?)\*(?!\*)/g, "$1<em>$2</em>");
  t = t.replace(/(^|[^a-zA-Z0-9_])_(?!\s)([^_\n]+?)_(?![a-zA-Z0-9_])/g, "$1<em>$2</em>");
  // strikethrough
  t = t.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  // @mentions
  t = t.replace(MENTION_RE, '<span class="mention">@$1</span>');
  return t;
}

// Null-char sentinel wraps code placeholders so they can't collide with
// literal user text (e.g. "C3") and passes through esc() untouched.
const S = String.fromCharCode(0);
const CODE_LINE = new RegExp(`^${S}C(\\d+)${S}$`);
const RESTORE_IC = new RegExp(`${S}I(\\d+)${S}`, "g");
const RESTORE_CODE = new RegExp(`${S}C(\\d+)${S}`, "g");
const isBlank = (l: string) => /^\s*$/.test(l);

/**
 * Render a lightweight but fairly complete Markdown subset to safe HTML.
 * Everything is HTML-escaped first; code spans/blocks are extracted before
 * escaping so their contents render verbatim. Supports: headings, bold,
 * italic, strikethrough, inline code, fenced code blocks (with language
 * label), links/autolinks, blockquotes, unordered & ordered lists, task
 * lists, GFM tables, horizontal rules, and @mentions.
 */
export function mdLite(src: string): string {
  if (src == null) return "";

  // 1. Pull out fenced code blocks first (verbatim, escaped once).
  const codeBlocks: string[] = [];
  let s = String(src).replace(
    /```([^\n`]*)\n?([\s\S]*?)```/g,
    (_m, lang: string, code: string) => {
      const i = codeBlocks.length;
      const lg = lang.trim();
      const label = lg ? `<span class="code-lang">${esc(lg)}</span>` : "";
      codeBlocks.push(
        `<pre class="code">${label}<code>${esc(code.replace(/\n$/, ""))}</code></pre>`
      );
      return `${S}C${i}${S}`;
    }
  );

  // 2. Pull out inline code spans (verbatim).
  const inlineCodes: string[] = [];
  s = s.replace(/`([^`\n]+)`/g, (_m, c: string) => {
    const i = inlineCodes.length;
    inlineCodes.push(`<code>${esc(c)}</code>`);
    return `${S}I${i}${S}`;
  });

  // 3. Escape the remaining text (sentinels survive untouched).
  s = esc(s);

  // 4. Block-level parse.
  const lines = s.split("\n");
  const at = (n: number): string => lines[n] ?? "";
  const out: string[] = [];
  let i = 0;

  const startsBlock = (l: string) =>
    CODE_LINE.test(l.trim()) ||
    /^(#{1,6})\s+/.test(l) ||
    /^\s*&gt;\s?/.test(l) ||
    /^\s*[-*+]\s+/.test(l) ||
    /^\s*\d+\.\s+/.test(l) ||
    /^\s*([-*_])(?:\s*\1){2,}\s*$/.test(l);

  while (i < lines.length) {
    const line = at(i);

    if (isBlank(line)) { i++; continue; }

    // standalone fenced-code placeholder
    if (CODE_LINE.test(line.trim())) { out.push(line.trim()); i++; continue; }

    // horizontal rule
    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) { out.push("<hr>"); i++; continue; }

    // heading
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      const lvl = (h[1] ?? "").length;
      out.push(`<h${lvl}>${inline((h[2] ?? "").trim())}</h${lvl}>`);
      i++;
      continue;
    }

    // blockquote (consecutive `>` lines — escaped to &gt;)
    if (/^\s*&gt;\s?/.test(line)) {
      const buf: string[] = [];
      while (i < lines.length && /^\s*&gt;\s?/.test(at(i))) {
        buf.push(at(i).replace(/^\s*&gt;\s?/, ""));
        i++;
      }
      out.push(`<blockquote>${inline(buf.join("<br>"))}</blockquote>`);
      continue;
    }

    // GFM table: header row + a separator row of ---|---
    if (line.includes("|") && i + 1 < lines.length &&
        /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(at(i + 1))) {
      const splitRow = (r: string) =>
        r.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
      const head = splitRow(line);
      i += 2; // skip header + separator
      const body: string[][] = [];
      while (i < lines.length && at(i).includes("|") && !isBlank(at(i))) {
        body.push(splitRow(at(i)));
        i++;
      }
      const thead = `<tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr>`;
      const tbody = body
        .map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`)
        .join("");
      out.push(`<div class="md-tablewrap"><table class="md-table"><thead>${thead}</thead><tbody>${tbody}</tbody></table></div>`);
      continue;
    }

    // unordered / task list
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(at(i))) {
        let item = at(i).replace(/^\s*[-*+]\s+/, "");
        const task = item.match(/^\[([ xX])\]\s+(.*)$/);
        if (task) {
          const checked = (task[1] ?? "").toLowerCase() === "x";
          item = `<span class="md-task ${checked ? "on" : ""}">${checked ? "☑" : "☐"}</span> ${task[2] ?? ""}`;
        }
        items.push(`<li>${inline(item)}</li>`);
        i++;
      }
      out.push(`<ul>${items.join("")}</ul>`);
      continue;
    }

    // ordered list
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(at(i))) {
        items.push(`<li>${inline(at(i).replace(/^\s*\d+\.\s+/, ""))}</li>`);
        i++;
      }
      out.push(`<ol>${items.join("")}</ol>`);
      continue;
    }

    // paragraph — gather until blank / next block
    const buf: string[] = [];
    while (i < lines.length && !isBlank(at(i)) && !startsBlock(at(i))) {
      buf.push(at(i));
      i++;
    }
    out.push(`<p>${inline(buf.join("<br>"))}</p>`);
  }

  // 5. Restore code placeholders.
  let html = out.join("\n");
  html = html.replace(RESTORE_IC, (_m, n) => inlineCodes[+n] ?? "");
  html = html.replace(RESTORE_CODE, (_m, n) => codeBlocks[+n] ?? "");
  return html;
}
