#!/usr/bin/env python3
"""Build the static GitHub Pages site in docs/ from the generated reports.

Inputs (all Markdown, produced by the other tools):
  matchups/weekNN.md     python3 matchups.py --full --markdown --out-dir matchups
  reports/waivers.md     python3 -m fantasy.monitor report --out reports/waivers.md

Outputs:
  docs/index.html            dashboard: this week's lineups + the waiver board
  docs/matchups/weekNN.html  every weekly matchup report
  docs/waivers.html          the full waiver & trade report
  docs/style.css, docs/.nojekyll

Anything else already in docs/ (for example the archived draft console) is
left alone. Only the Python standard library is required.
"""

from __future__ import annotations

import glob
import html
import os
import re
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, "docs")
SITE_TITLE = "Zebras Shooting Heroin"


# ---------------------------------------------------------------------------
# A small Markdown -> HTML converter covering what the reports use
# ---------------------------------------------------------------------------

def inline(text: str) -> str:
    s = html.escape(text, quote=False)
    s = s.replace("&lt;br&gt;", "<br>")
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"<em>\1</em>", s)
    return s


def _table(lines: list[str]) -> str:
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]
    aligns = []
    if len(rows) > 1 and all(re.fullmatch(r":?-+:?", c or "-") for c in rows[1]):
        for c in rows[1]:
            aligns.append("center" if c.startswith(":") and c.endswith(":") else
                          "right" if c.endswith(":") else "left")
        header, body = rows[0], rows[2:]
    else:
        header, body = rows[0], rows[1:]
    aligns += ["left"] * (len(header) - len(aligns))

    def cells(row, tag):
        out = []
        for i, c in enumerate(row):
            a = aligns[i] if i < len(aligns) else "left"
            cls = ""
            if tag == "td":
                for word in ("TOUGH", "SOFT", "NEUTRAL"):
                    if word in c:
                        cls = f' class="v-{word.lower()}"'
                        break
            out.append(f'<{tag}{cls} style="text-align:{a}">{inline(c)}</{tag}>')
        return "".join(out)

    out = ['<div class="table-wrap"><table>', "<thead><tr>" + cells(header, "th") + "</tr></thead>", "<tbody>"]
    for r in body:
        out.append("<tr>" + cells(r, "td") + "</tr>")
    out.append("</tbody></table></div>")
    return "\n".join(out)


def md_to_html(md: str) -> str:
    out: list[str] = []
    lines = md.splitlines()
    i = 0
    para: list[str] = []
    list_stack: list[int] = []   # indent levels of open <ul>s

    def flush_para():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()

    def close_lists(to_level: int = -1):
        while list_stack and list_stack[-1] > to_level:
            out.append("</li></ul>")
            list_stack.pop()

    while i < len(lines):
        ln = lines[i]
        stripped = ln.strip()
        if stripped.startswith("```"):
            flush_para(); close_lists()
            j = i + 1
            code = []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                code.append(lines[j]); j += 1
            out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
            i = j + 1
            continue
        if not stripped:
            flush_para(); close_lists()
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", stripped)
        if m:
            flush_para(); close_lists()
            level = len(m.group(1))
            title = m.group(2)
            out.append(f'<h{level} id="{slug(title)}">{inline(title)}</h{level}>')
            i += 1
            continue
        if stripped.startswith("|"):
            flush_para(); close_lists()
            j = i
            block = []
            while j < len(lines) and lines[j].strip().startswith("|"):
                block.append(lines[j]); j += 1
            out.append(_table(block))
            i = j
            continue
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            flush_para(); close_lists()
            out.append("<hr>")
            i += 1
            continue
        m = re.match(r"^(\s*)[-*]\s+(.*)", ln)
        if m:
            flush_para()
            level = len(m.group(1).replace("\t", "  ")) // 2
            if not list_stack or level > list_stack[-1]:
                out.append("<ul><li>" + inline(m.group(2)))
                list_stack.append(level)
            else:
                close_lists(level)
                if list_stack and list_stack[-1] == level:
                    out.append("</li><li>" + inline(m.group(2)))
                else:
                    out.append("<ul><li>" + inline(m.group(2)))
                    list_stack.append(level)
            i += 1
            continue
        if list_stack and ln.startswith(" "):
            # continuation line inside a list item (e.g. the "↳ blurb" lines)
            out.append('<br><span class="cont">' + inline(stripped) + "</span>")
            i += 1
            continue
        close_lists()
        para.append(stripped)
        i += 1
    flush_para(); close_lists()
    return "\n".join(out)


def slug(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_]+", "-", s) or "section"


# ---------------------------------------------------------------------------
# Report handling
# ---------------------------------------------------------------------------

def read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def split_sections(md: str) -> tuple[str, str, list[tuple[str, str]]]:
    """Returns (title, preamble, [(heading, body)...]) split on '## ' headings."""
    title, pre, sections = "", [], []
    cur_head, cur_body = None, []
    for ln in md.splitlines():
        if ln.startswith("# ") and not title:
            title = ln[2:].strip()
            continue
        if ln.startswith("## "):
            if cur_head is not None:
                sections.append((cur_head, "\n".join(cur_body)))
            cur_head, cur_body = ln[3:].strip(), []
            continue
        (cur_body if cur_head is not None else pre).append(ln)
    if cur_head is not None:
        sections.append((cur_head, "\n".join(cur_body)))
    return title, "\n".join(pre), sections


def week_files() -> list[tuple[int, str]]:
    out = []
    for p in glob.glob(os.path.join(ROOT, "matchups", "week*.md")):
        m = re.search(r"week(\d+)\.md$", p)
        if m:
            out.append((int(m.group(1)), p))
    return sorted(out)


# ---------------------------------------------------------------------------
# Page shell
# ---------------------------------------------------------------------------

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1a1d23;--muted:#5d6570;--line:#e3e6ea;--accent:#1f6feb;--soft:#e9f5ec;--tough:#fdecec;--neutral:#fff6e0}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a20;--ink:#e6e8eb;--muted:#9aa3ad;--line:#2a2f37;--accent:#6ea8ff;--soft:#12301a;--tough:#3a1717;--neutral:#3a2f12}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
a{color:var(--accent)}header.top{background:var(--card);border-bottom:1px solid var(--line);padding:14px 20px;display:flex;flex-wrap:wrap;gap:8px 22px;align-items:baseline}
header.top .brand{font-weight:700;font-size:17px;color:var(--ink);text-decoration:none}header.top nav a{margin-right:14px;text-decoration:none;color:var(--muted)}header.top nav a.active{color:var(--accent);font-weight:600}
main{max-width:1180px;margin:0 auto;padding:20px}h1{font-size:26px;margin:8px 0 4px}h2{font-size:20px;margin:30px 0 8px;padding-bottom:4px;border-bottom:1px solid var(--line)}h3{font-size:16px;margin:20px 0 6px}
.meta{color:var(--muted);font-size:13px;margin-bottom:10px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:14px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}.card h2{margin-top:0;border:0;padding:0}
.table-wrap{overflow-x:auto;margin:10px 0}table{border-collapse:collapse;width:100%;font-size:13.5px}th,td{padding:6px 9px;border-bottom:1px solid var(--line);vertical-align:top}th{background:var(--bg);position:sticky;top:0;font-weight:600;white-space:nowrap}
td.v-tough{background:var(--tough)}td.v-soft{background:var(--soft)}td.v-neutral{background:var(--neutral)}
ul{padding-left:20px}li{margin:4px 0}.cont{color:var(--muted);font-size:13px}code{background:var(--bg);padding:1px 5px;border-radius:4px;font-size:12.5px}
pre{background:var(--bg);padding:12px;border-radius:8px;overflow-x:auto;font-size:12.5px}.more{margin:6px 0 0;font-size:13px}
footer{color:var(--muted);font-size:12px;text-align:center;padding:30px 0}
"""


def page(title: str, body: str, active: str, depth: int = 0, generated: str = "") -> str:
    rel = "../" * depth
    nav = [("index.html", "Dashboard", "home"), ("waivers.html", "Waiver wire", "waivers"),
           ("weeks.html", "All weeks", "weeks"), ("draft-live.html", "Draft console (archive)", "draft")]
    has_draft = os.path.exists(os.path.join(DOCS, "draft-live.html"))
    links = []
    for href, label, key in nav:
        if key == "draft" and not has_draft:
            continue
        cls = ' class="active"' if key == active else ""
        links.append(f'<a href="{rel}{href}"{cls}>{label}</a>')
    links = "".join(links)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · {SITE_TITLE}</title><link rel="stylesheet" href="{rel}style.css"></head>
<body><header class="top"><a class="brand" href="{rel}index.html">{SITE_TITLE}</a><nav>{links}</nav></header>
<main>{body}</main>
<footer>Built {generated}. Data: Sleeper, nflverse, Pro Football Reference, ESPN, RotoWire. Regenerated automatically by GitHub Actions.</footer>
</body></html>"""


def write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def main() -> int:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    os.makedirs(DOCS, exist_ok=True)
    write(os.path.join(DOCS, ".nojekyll"), "")
    write(os.path.join(DOCS, "style.css"), CSS.strip() + "\n")

    weeks = week_files()
    latest_week, latest_md = (weeks[-1][0], read(weeks[-1][1])) if weeks else (None, None)
    waivers_md = read(os.path.join(ROOT, "reports", "waivers.md"))

    # Weekly matchup pages
    for wk, path in weeks:
        md = read(path) or ""
        title, pre, secs = split_sections(md)
        body = f"<h1>{inline(title)}</h1>" + md_to_html(pre) + "".join(
            f'<h2 id="{slug(h)}">{inline(h)}</h2>' + md_to_html(b) for h, b in secs)
        write(os.path.join(DOCS, "matchups", f"week{wk:02d}.html"),
              page(f"Week {wk} matchups", body, "weeks", depth=1, generated=generated))

    # Weeks index
    items = "".join(f'<li><a href="matchups/week{wk:02d}.html">Week {wk}</a></li>' for wk, _ in reversed(weeks))
    write(os.path.join(DOCS, "weeks.html"),
          page("All weeks", f"<h1>Weekly matchup reports</h1><ul>{items or '<li>No reports yet.</li>'}</ul>",
               "weeks", generated=generated))

    # Waiver page
    if waivers_md:
        title, pre, secs = split_sections(waivers_md)
        body = f"<h1>{inline(title)}</h1>" + md_to_html(pre) + "".join(
            f'<h2 id="{slug(h)}">{inline(h)}</h2>' + md_to_html(b) for h, b in secs)
    else:
        body = "<h1>Waiver wire</h1><p>No waiver report has been generated yet.</p>"
    write(os.path.join(DOCS, "waivers.html"), page("Waiver wire", body, "waivers", generated=generated))

    # Dashboard
    parts = [f"<h1>{SITE_TITLE}</h1><p class='meta'>Lineup matchups and waiver wire for the Zebras Shooting Heroin "
             f"league. Updated {generated}.</p>"]
    if latest_md:
        title, pre, secs = split_sections(latest_md)
        parts.append(f"<h2>{inline(title)}</h2>" + md_to_html(pre))
        for h, b in secs:
            if h.startswith("All NFL") or h.startswith("Team defenses") or h.startswith("Starting CB"):
                break
            parts.append(f'<div class="card"><h2>{inline(h)}</h2>{md_to_html(b)}</div>')
        parts.append(f'<p class="more"><a href="matchups/week{latest_week:02d}.html">Full week {latest_week} report</a>: '
                     f'every NFL starter, all 32 team defenses, and the CB leaderboard.</p>')
    else:
        parts.append("<p>No matchup report has been generated yet.</p>")
    if waivers_md:
        title, pre, secs = split_sections(waivers_md)
        parts.append(f"<h2>{inline(title)}</h2>" + md_to_html(pre))
        wanted = [s for s in secs if s[0].startswith("Since last run") or s[0].startswith("Waiver board")]
        for h, b in wanted:
            parts.append(f'<div class="card"><h2>{inline(h)}</h2>{md_to_html(b)}</div>')
        parts.append('<p class="more"><a href="waivers.html">Full waiver &amp; trade report</a>: '
                     'handcuffs, roster strength, trade targets and news.</p>')
    write(os.path.join(DOCS, "index.html"), page("Dashboard", "".join(parts), "home", generated=generated))
    print(f"built docs/ ({len(weeks)} week(s), waivers: {'yes' if waivers_md else 'no'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
