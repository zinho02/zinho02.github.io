#!/usr/bin/env python3
"""
site.py — content tooling for the personal website.

The site is a single static `index.html`, but the parts that change often
(publications, about text, links, header animation) live in `data/` and are
rendered into marked regions of `index.html` by this script.

    data/site.json          identity, about paragraphs, contact links, animation
    data/publications.json  ordered publication metadata
    data/bib/<key>.bib      one BibTeX entry per publication

Typical use:

    ./scripts/site.py pub add --bib ~/Downloads/paper.bib --pdf URL --code URL
    ./scripts/site.py pub edit saldanha2026lut --venue "..."
    ./scripts/site.py pub rm saldanha2026lut
    ./scripts/site.py pub move saldanha2026lut --top
    ./scripts/site.py build
    ./scripts/site.py serve

Every mutating command rebuilds `index.html` automatically. Nothing outside the
marked regions is ever touched, so hand edits to the rest of the page survive.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
BIB_DIR = os.path.join(DATA, "bib")
SITE_JSON = os.path.join(DATA, "site.json")
PUBS_JSON = os.path.join(DATA, "publications.json")
INDEX = os.path.join(ROOT, "index.html")

BEGIN = "<!-- site.py:begin %s -->"
END = "<!-- site.py:end %s -->"


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def die(msg):
    print("error: %s" % msg, file=sys.stderr)
    raise SystemExit(1)


def warn(msg):
    print("warning: %s" % msg, file=sys.stderr)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def load_json(path):
    try:
        return json.loads(read(path))
    except FileNotFoundError:
        die("missing %s" % os.path.relpath(path, ROOT))
    except json.JSONDecodeError as exc:
        die("%s is not valid JSON: %s" % (os.path.relpath(path, ROOT), exc))


# Objects made only of scalars are written on one line when they fit, so
# link/photo/cv entries stay as readable as when they were typed by hand.
FLAT_OBJECT = re.compile(r"\{\s*\n\s*((?:\"[^\"]*\"\s*:\s*(?:\"(?:[^\"\\\\]|\\\\.)*\"|-?[\d.eE+]+|true|false|null)\s*,?\s*\n?\s*)+)\}")


def _collapse(match, width):
    body = " ".join(part.strip() for part in match.group(1).strip().splitlines())
    line = "{ %s }" % body
    indent = match.start() - (match.string.rfind("\n", 0, match.start()) + 1)
    return line if indent + len(line) <= width else match.group(0)


TOP_KEY = re.compile(r'^  "([^"]+)":')


def _spaced_keys(path):
    """Top-level keys that the file on disk sets off with a blank line."""
    try:
        lines = read(path).splitlines()
    except FileNotFoundError:
        return set()
    keys, previous = set(), ""
    for line in lines:
        match = TOP_KEY.match(line)
        if match and not previous.strip():
            keys.add(match.group(1))
        previous = line
    return keys


def save_json(path, obj, width=140):
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    while True:
        collapsed = FLAT_OBJECT.sub(lambda m: _collapse(m, width), text)
        if collapsed == text:
            break
        text = collapsed

    # Put back the blank lines that grouped the file's sections; JSON cannot
    # carry them, but they are how the file was meant to be read.
    spaced = _spaced_keys(path)
    if spaced:
        out = []
        for line in text.splitlines():
            match = TOP_KEY.match(line)
            if match and match.group(1) in spaced and out and out[-1].strip():
                out.append("")
            out.append(line)
        text = "\n".join(out)
    write(path, text + "\n")


def esc(text):
    """Escape text for use as HTML element content."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def eattr(text):
    """Escape text for use inside a double-quoted HTML attribute."""
    return esc(text).replace('"', "&quot;")


def indent_block(lines, pad):
    return "\n".join((pad + ln) if ln else "" for ln in lines)


# --------------------------------------------------------------------------
# BibTeX
# --------------------------------------------------------------------------

ACCENTS = {
    "'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308",
    "~": "\u0303", "c": "\u0327", "v": "\u030c", "=": "\u0304",
    ".": "\u0307", "u": "\u0306", "H": "\u030b", "k": "\u0328",
    "r": "\u030a",
}


def delatex(text):
    """Turn common LaTeX escapes into real characters, for display strings."""
    out = text
    out = out.replace("\\i", "\u0131").replace("\\j", "\u0237")
    # {\'o}  {\'{o}}  \'o  \c{c}  {\c{c}}
    pattern = re.compile(r"\\([\'`^\"~cv=.uHkr])\s*\{?([A-Za-z\u0131\u0237])\}?")
    out = pattern.sub(lambda m: m.group(2) + ACCENTS[m.group(1)], out)
    for src, dst in (("\\ss", "\u00df"), ("\\o", "\u00f8"), ("\\O", "\u00d8"),
                     ("\\aa", "\u00e5"), ("\\AA", "\u00c5"), ("\\&", "&"),
                     ("--", "\u2013"), ("~", " ")):
        out = out.replace(src, dst)
    out = out.replace("{", "").replace("}", "")
    out = unicodedata.normalize("NFC", out)
    return re.sub(r"\s+", " ", out).strip()


def parse_bib(text):
    """Parse BibTeX source into [{'type':..,'key':..,'fields':[(name,value)]}]."""
    entries = []
    i, n = 0, len(text)
    while True:
        at = text.find("@", i)
        if at < 0:
            break
        j = at + 1
        while j < n and (text[j].isalpha()):
            j += 1
        etype = text[at + 1:j].strip().lower()
        while j < n and text[j].isspace():
            j += 1
        if j >= n or text[j] not in "{(":
            i = at + 1
            continue
        opener, closer = text[j], ("}" if text[j] == "{" else ")")
        j += 1
        if etype in ("comment", "preamble", "string"):
            depth = 1
            while j < n and depth:
                if text[j] == opener:
                    depth += 1
                elif text[j] == closer:
                    depth -= 1
                j += 1
            i = j
            continue

        k = j
        while k < n and text[k] not in ",}":
            k += 1
        key = text[j:k].strip()
        j = k + 1 if k < n and text[k] == "," else k

        fields = []
        while j < n:
            while j < n and (text[j].isspace() or text[j] == ","):
                j += 1
            if j >= n or text[j] == closer:
                j += 1
                break
            k = j
            while k < n and text[k] not in "=,}":
                k += 1
            name = text[j:k].strip().lower()
            if k >= n or text[k] != "=":
                j = k + 1
                continue
            j = k + 1
            while j < n and text[j].isspace():
                j += 1
            if j < n and text[j] == "{":
                depth, start = 1, j + 1
                j += 1
                while j < n and depth:
                    if text[j] == "\\":
                        j += 2
                        continue
                    if text[j] == "{":
                        depth += 1
                    elif text[j] == "}":
                        depth -= 1
                    j += 1
                value = text[start:j - 1]
            elif j < n and text[j] == '"':
                depth, start = 0, j + 1
                j += 1
                while j < n:
                    if text[j] == "\\":
                        j += 2
                        continue
                    if text[j] == "{":
                        depth += 1
                    elif text[j] == "}":
                        depth -= 1
                    elif text[j] == '"' and depth == 0:
                        break
                    j += 1
                value = text[start:j]
                j += 1
            else:
                start = j
                while j < n and text[j] not in ",}":
                    j += 1
                value = text[start:j].strip()
            if name:
                fields.append((name, re.sub(r"\s+", " ", value).strip()))
        entries.append({"type": etype, "key": key, "fields": fields})
        i = j
    return entries


def format_bib(entry):
    """Render a parsed entry back to normalized BibTeX text."""
    lines = ["@%s{%s," % (entry["type"], entry["key"])]
    for idx, (name, value) in enumerate(entry["fields"]):
        tail = "}" if idx == len(entry["fields"]) - 1 else "},"
        lines.append("  %s={%s%s" % (name, value, tail))
    lines.append("}")
    return "\n".join(lines) + "\n"


def highlight_bib(entry):
    """Render a parsed entry as the syntax-highlighted markup the page uses.

    The plain-text content of the result is exactly `format_bib(entry)` minus
    the trailing newline, which is what the copy button puts on the clipboard.
    """
    def span(cls, text):
        return '<span class="bibtex-%s">%s</span>' % (cls, esc(text))

    out = [span("punct", "@") + span("type", entry["type"]) +
           span("punct", "{") + span("value", entry["key"]) + span("punct", ",")]
    for idx, (name, value) in enumerate(entry["fields"]):
        tail = "}" if idx == len(entry["fields"]) - 1 else "},"
        out.append("  " + span("field", name) + span("punct", "=") +
                   span("punct", "{") + span("value", value) + span("punct", tail))
    out.append(span("punct", "}"))
    return "\n".join(out)


def split_authors(field):
    """'Last, First and Other, N' -> ['First Last', 'N Other'] (display form)."""
    parts, depth, buf = [], 0, ""
    tokens = re.split(r"(\s+and\s+)", field)
    for tok in tokens:
        if re.fullmatch(r"\s+and\s+", tok) and depth == 0:
            parts.append(buf)
            buf = ""
            continue
        depth += tok.count("{") - tok.count("}")
        buf += tok
    if buf.strip():
        parts.append(buf)

    names = []
    for raw in parts:
        raw = raw.strip()
        if not raw:
            continue
        if "," in raw:
            last, _, first = raw.partition(",")
            raw = "%s %s" % (first.strip(), last.strip())
        names.append(delatex(raw))
    return names


def bib_path(key):
    return os.path.join(BIB_DIR, "%s.bib" % key)


def load_bib_entry(key, required=True):
    path = bib_path(key)
    if not os.path.exists(path):
        if required:
            die("no BibTeX file for '%s' (expected %s)"
                % (key, os.path.relpath(path, ROOT)))
        return None
    entries = parse_bib(read(path))
    if not entries:
        die("%s contains no BibTeX entry" % os.path.relpath(path, ROOT))
    return entries[0]


# --------------------------------------------------------------------------
# icons
# --------------------------------------------------------------------------

ICONS = {
    "email": ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
              'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
              '<rect x="3" y="5" width="18" height="14" rx="2"/>'
              '<path d="m3 7 9 6 9-6"/></svg>'),
    "scholar": ('<svg viewBox="0 0 24 24" fill="currentColor" stroke="none">'
                '<path d="M5.242 13.769L0 9.5 12 0l12 9.5-5.242 4.269C17.548 11.24 '
                '14.978 9.5 12 9.5c-2.977 0-5.548 1.74-6.758 4.269zM12 10a7 7 0 1 0 '
                '0 14 7 7 0 0 0 0-14z"/></svg>'),
    "github": ('<svg viewBox="0 0 24 24" fill="currentColor" stroke="none">'
               '<path d="M12 .5C5.73.5.5 5.73.5 12c0 5.08 3.29 9.39 7.86 10.91.58.11.79-.25.79-.56 '
               '0-.27-.01-1.17-.02-2.12-3.2.7-3.88-1.36-3.88-1.36-.52-1.33-1.28-1.68-1.28-1.68-1.04-.72.08-.7.08-.7 '
               '1.15.08 1.76 1.19 1.76 1.19 1.03 1.76 2.69 1.25 3.34.96.1-.75.4-1.25.73-1.54-2.55-.29-5.24-1.28-5.24-5.7 '
               '0-1.26.45-2.29 1.19-3.09-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18a11 11 0 0 1 5.79 0c2.2-1.49 '
               '3.17-1.18 3.17-1.18.63 1.59.23 2.76.11 3.05.74.8 1.19 1.83 1.19 3.09 0 4.43-2.69 5.41-5.25 '
               '5.69.41.36.78 1.06.78 2.15 0 1.55-.01 2.8-.01 3.18 0 .31.21.68.8.56A10.52 10.52 0 0 0 23.5 12C23.5 '
               '5.73 18.27.5 12 .5Z"/></svg>'),
    "linkedin": ('<svg viewBox="0 0 24 24" fill="currentColor" stroke="none">'
                 '<path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 '
                 '0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 '
                 '3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 '
                 '0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 '
                 '2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 '
                 '1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 '
                 '.774 23.2 0 22.222 0h.003z"/></svg>'),
    "globe": ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
              'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
              '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/>'
              '<path d="M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18Z"/></svg>'),
    "file": ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
             '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/>'
             '<path d="M14 2v6h6"/></svg>'),
    "rss": ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M4 11a9 9 0 0 1 9 9"/><path d="M4 4a16 16 0 0 1 16 16"/>'
            '<circle cx="5" cy="19" r="1.5" fill="currentColor"/></svg>'),
}

COPY_ICON = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
             '<rect x="9" y="9" width="13" height="13" rx="2"/>'
             '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>')

CV_ICON = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
           'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
           '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/>'
           '<path d="M14 2v6h6"/><path d="M12 11v6"/><path d="m9 14 3 3 3-3"/></svg>')


def icon_markup(item):
    if item.get("svg"):
        return item["svg"]
    name = item.get("icon", "")
    if name not in ICONS:
        die("unknown icon %r (run `site.py icons`, or give an inline \"svg\")" % name)
    return ICONS[name]


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def norm_name(name):
    text = unicodedata.normalize("NFKD", name.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z]+", "", text)


def render_authors(authors, aliases):
    keys = {norm_name(a) for a in aliases}
    out = []
    for name in authors:
        if norm_name(name) in keys:
            out.append('<span class="me">%s</span>' % esc(name))
        else:
            out.append(esc(name))
    return ", ".join(out)


def render_head(site):
    return [
        "<title>%s</title>" % esc(site.get("page_title", site.get("name", ""))),
        '<meta name="description" content="%s">' % eattr(site.get("meta_description", "")),
    ]


def render_hero(site):
    photo = site.get("photo") or {}
    cv = site.get("cv") or {}
    lines = ['<div class="hero-inner">']
    if photo.get("src"):
        size = photo.get("size", 280)
        lines += [
            '  <div class="hero-photo">',
            '    <img class="photo" src="%s" alt="%s" width="%s" height="%s">'
            % (eattr(photo["src"]), eattr(photo.get("alt", site.get("name", ""))), size, size),
            "  </div>",
        ]
    lines += [
        '  <div class="hero-content">',
        '    <h1 class="name">%s</h1>' % esc(site.get("name", "")),
    ]
    if site.get("role"):
        lines.append('    <p class="role">%s</p>' % esc(site["role"]))
    if cv.get("href"):
        label = cv.get("label", "Download CV")
        lines += [
            '    <div class="hero-links">',
            '      <a class="btn-cv" href="%s" aria-label="%s" title="%s">'
            % (eattr(cv["href"]), eattr(label), eattr(label)),
            "        %s" % CV_ICON,
            "        <span>%s</span>" % esc(label),
            "      </a>",
            "    </div>",
        ]
    lines += ["  </div>", "</div>"]
    return lines


def render_about(site):
    return ['<p class="tight">%s</p>' % para for para in site.get("about", [])]


def render_publications(site, pubs):
    aliases = site.get("me_aliases", [])
    new_tab = bool(site.get("external_links_new_tab", False))
    attrs = ' target="_blank" rel="noopener"' if new_tab else ""
    lines, group = [], None

    for pub in pubs:
        if pub.get("group") and pub["group"] != group:
            group = pub["group"]
            lines.append('<p class="pubs-head">%s</p>' % esc(group))

        entry = load_bib_entry(pub["key"], required=False)
        lines.append('<div class="pub">')
        lines.append('  <p class="pub-title">%s</p>' % esc(pub.get("title", "")))
        lines.append('  <p class="pub-authors">%s</p>'
                     % render_authors(pub.get("authors", []), aliases))
        if pub.get("venue"):
            lines.append('  <p class="pub-venue">%s</p>' % esc(pub["venue"]))

        lines.append('  <div class="pub-links">')
        if entry:
            lines.append('    <a href="#" class="bibtex-trigger" aria-expanded="false">BibTeX</a>')
        for link in pub.get("links", []):
            if not link.get("href"):
                continue
            lines.append('    <a href="%s"%s>%s</a>'
                         % (eattr(link["href"]), attrs, esc(link.get("label", "Link"))))
        lines.append("  </div>")

        if entry:
            code = highlight_bib(entry)
            lines += [
                '  <div class="bibtex-panel">',
                '    <div class="bibtex-panel-inner">',
                '      <pre class="bibtex-code"><code>%s</code></pre>' % code,
                '      <button class="bibtex-copy-btn" type="button" aria-label="Copy BibTeX">',
                "        %s" % COPY_ICON,
                "      </button>",
                "    </div>",
                "  </div>",
            ]
        lines.append("</div>")
    return lines


def render_contact(site):
    lines = ['<div class="contact-row">']
    for item in site.get("contact", []):
        label = item.get("label", "")
        lines += [
            '  <a class="icon-btn" href="%s" aria-label="%s" title="%s">'
            % (eattr(item.get("href", "#")), eattr(label), eattr(label)),
            "    %s" % icon_markup(item),
            "  </a>",
        ]
    lines.append("</div>")
    return lines


def render_animation_config(site):
    cfg = site.get("animation") or {}
    return ["<script>",
            "  window.ISO_CONFIG = %s;" % json.dumps(cfg, indent=2).replace("\n", "\n  "),
            "</script>"]


REGIONS = {
    "head": render_head,
    "hero": render_hero,
    "about": render_about,
    "publications": render_publications,
    "contact": render_contact,
    "animation-config": render_animation_config,
}


def splice(html, name, lines):
    begin, end = BEGIN % name, END % name
    pattern = re.compile(
        r"([ \t]*)" + re.escape(begin) + r".*?" + re.escape(end),
        re.DOTALL)
    match = pattern.search(html)
    if not match:
        die("index.html has no '%s' region (missing %s ... %s)" % (name, begin, end))
    pad = match.group(1)
    body = indent_block(lines, pad)
    replacement = pad + begin + ("\n" + body if body else "") + "\n" + pad + end
    return html[:match.start()] + replacement + html[match.end():]


def build_html():
    site = load_json(SITE_JSON)
    pubs = load_json(PUBS_JSON).get("publications", [])
    html = read(INDEX)
    for name, fn in REGIONS.items():
        lines = fn(site, pubs) if name == "publications" else fn(site)
        html = splice(html, name, lines)
    return html


def cmd_build(args):
    new = build_html()
    old = read(INDEX)
    if new == old:
        print("index.html is up to date")
        return 0
    if args.check:
        print("index.html is out of date — run: ./scripts/site.py build", file=sys.stderr)
        return 1
    write(INDEX, new)
    print("index.html updated")
    return 0


# --------------------------------------------------------------------------
# publication commands
# --------------------------------------------------------------------------

def load_pubs():
    doc = load_json(PUBS_JSON)
    doc.setdefault("publications", [])
    return doc


def save_pubs(doc):
    save_json(PUBS_JSON, doc)


def find_pub(pubs, key):
    for idx, pub in enumerate(pubs):
        if pub["key"] == key:
            return idx
    die("no publication with key '%s' (run `site.py pub list`)" % key)


def parse_link_args(args, existing=None):
    """Build the links list from --pdf/--html/--code/--link flags."""
    links = list(existing or [])

    def upsert(label, href):
        for link in links:
            if link.get("label", "").lower() == label.lower():
                if href:
                    link["href"] = href
                else:
                    links.remove(link)
                return
        if href:
            links.append({"label": label, "href": href})

    for label, value in (("PDF", args.pdf), ("HTML", args.html),
                         ("Code", args.code), ("Slides", args.slides),
                         ("DOI", args.doi)):
        if value is not None:
            upsert(label, value)
    for raw in (args.link or []):
        label, sep, href = raw.partition("=")
        if not sep:
            die("--link expects Label=URL, got %r" % raw)
        upsert(label.strip(), href.strip())
    return links


def bib_text_from_args(args):
    if not args.bib:
        return None
    if args.bib == "-":
        print("Paste BibTeX, then Ctrl-D:", file=sys.stderr)
        return sys.stdin.read()
    if not os.path.exists(args.bib):
        die("no such file: %s" % args.bib)
    return read(args.bib)


def cmd_pub_list(args):
    pubs = load_pubs()["publications"]
    if not pubs:
        print("(no publications)")
        return 0
    width = max(len(p["key"]) for p in pubs)
    for idx, pub in enumerate(pubs, 1):
        has_bib = "bib" if os.path.exists(bib_path(pub["key"])) else "   "
        print("%2d. [%s] %-*s  %s" % (idx, has_bib, width, pub["key"], pub.get("title", "")))
        print("    %s  ·  %s" % (", ".join(pub.get("authors", [])), pub.get("venue", "")))
    return 0


def cmd_pub_show(args):
    doc = load_pubs()
    idx = find_pub(doc["publications"], args.key)
    print(json.dumps(doc["publications"][idx], indent=2, ensure_ascii=False))
    entry = load_bib_entry(args.key, required=False)
    if entry:
        print()
        print(format_bib(entry), end="")
    return 0


def cmd_pub_add(args):
    doc = load_pubs()
    pubs = doc["publications"]

    bibtext = bib_text_from_args(args)
    entry = None
    if bibtext:
        entries = parse_bib(bibtext)
        if not entries:
            die("no BibTeX entry found in the input")
        if len(entries) > 1:
            warn("input has %d entries; using the first (%s)" % (len(entries), entries[0]["key"]))
        entry = entries[0]

    key = args.key or (entry["key"] if entry else None)
    if not key:
        die("need --key (or --bib, to take the key from the BibTeX entry)")
    if any(p["key"] == key for p in pubs):
        die("publication '%s' already exists (use `pub edit %s`)" % (key, key))

    fields = dict(entry["fields"]) if entry else {}
    title = args.title or delatex(fields.get("title", ""))
    authors = (split_arg_list(args.authors) if args.authors
               else (split_authors(fields.get("author", "")) if fields else []))
    venue = args.venue or delatex(
        fields.get("booktitle") or fields.get("journal") or fields.get("publisher") or "")
    if fields.get("year") and venue and fields["year"] not in venue:
        venue = "%s %s" % (venue, fields["year"])

    if not title:
        die("need --title (or a BibTeX entry with a title field)")

    pub = {"key": key, "title": title, "authors": authors, "venue": venue,
           "links": parse_link_args(args)}
    if args.group:
        pub["group"] = args.group

    pos = resolve_position(args, len(pubs), default="bottom")
    pubs.insert(pos, pub)

    if entry:
        entry["key"] = key
        os.makedirs(BIB_DIR, exist_ok=True)
        write(bib_path(key), format_bib(entry))
        print("wrote %s" % os.path.relpath(bib_path(key), ROOT))

    save_pubs(doc)
    print("added '%s' at position %d" % (key, pos + 1))
    if not entry:
        print("note: no BibTeX yet — add %s and rebuild, or re-run with --bib"
              % os.path.relpath(bib_path(key), ROOT))
    return maybe_build(args)


def cmd_pub_edit(args):
    doc = load_pubs()
    pubs = doc["publications"]
    idx = find_pub(pubs, args.key)
    pub = pubs[idx]

    bibtext = bib_text_from_args(args)
    if bibtext:
        entries = parse_bib(bibtext)
        if not entries:
            die("no BibTeX entry found in the input")
        entry = entries[0]
        entry["key"] = pub["key"]
        os.makedirs(BIB_DIR, exist_ok=True)
        write(bib_path(pub["key"]), format_bib(entry))
        print("updated %s" % os.path.relpath(bib_path(pub["key"]), ROOT))

    if args.title:
        pub["title"] = args.title
    if args.authors:
        pub["authors"] = split_arg_list(args.authors)
    if args.venue:
        pub["venue"] = args.venue
    if args.group is not None:
        if args.group:
            pub["group"] = args.group
        else:
            pub.pop("group", None)
    pub["links"] = parse_link_args(args, pub.get("links", []))

    if args.rename:
        if any(p["key"] == args.rename for p in pubs):
            die("key '%s' is already taken" % args.rename)
        old = bib_path(pub["key"])
        if os.path.exists(old):
            entry = parse_bib(read(old))[0]
            entry["key"] = args.rename
            write(bib_path(args.rename), format_bib(entry))
            os.remove(old)
        pub["key"] = args.rename

    save_pubs(doc)
    print("updated '%s'" % pub["key"])
    return maybe_build(args)


def cmd_pub_rm(args):
    doc = load_pubs()
    pubs = doc["publications"]
    idx = find_pub(pubs, args.key)
    pub = pubs[idx]
    if not args.yes:
        reply = input("remove '%s' (%s)? [y/N] " % (pub["key"], pub.get("title", "")))
        if reply.strip().lower() not in ("y", "yes"):
            print("aborted")
            return 1
    pubs.pop(idx)
    path = bib_path(pub["key"])
    if os.path.exists(path):
        if args.keep_bib:
            print("kept %s" % os.path.relpath(path, ROOT))
        else:
            os.remove(path)
            print("removed %s" % os.path.relpath(path, ROOT))
    save_pubs(doc)
    print("removed '%s'" % pub["key"])
    return maybe_build(args)


def cmd_pub_move(args):
    doc = load_pubs()
    pubs = doc["publications"]
    idx = find_pub(pubs, args.key)
    pub = pubs.pop(idx)
    if args.up:
        pos = max(0, idx - 1)
    elif args.down:
        pos = min(len(pubs), idx + 1)
    else:
        pos = resolve_position(args, len(pubs), default="top")
    pubs.insert(pos, pub)
    save_pubs(doc)
    print("'%s' is now #%d" % (pub["key"], pos + 1))
    return maybe_build(args)


def cmd_pub_sort(args):
    doc = load_pubs()

    def year_of(pub):
        entry = load_bib_entry(pub["key"], required=False)
        if entry:
            fields = dict(entry["fields"])
            if fields.get("year", "").strip().isdigit():
                return int(fields["year"].strip())
        match = re.search(r"((?:19|20)\d{2})", pub.get("venue", ""))
        return int(match.group(1)) if match else 0

    keyfn = {"year": year_of,
             "title": lambda p: p.get("title", "").lower(),
             "key": lambda p: p["key"]}[args.by]
    doc["publications"].sort(key=keyfn, reverse=not args.ascending)
    save_pubs(doc)
    print("sorted by %s (%s)" % (args.by, "ascending" if args.ascending else "descending"))
    return maybe_build(args)


def split_arg_list(text):
    sep = ";" if ";" in text else ","
    return [part.strip() for part in text.split(sep) if part.strip()]


def resolve_position(args, count, default):
    if getattr(args, "top", False):
        return 0
    if getattr(args, "bottom", False):
        return count
    if getattr(args, "position", None):
        return max(0, min(count, args.position - 1))
    return 0 if default == "top" else count


def maybe_build(args):
    if getattr(args, "no_build", False):
        return 0
    new = build_html()
    if new != read(INDEX):
        write(INDEX, new)
        print("index.html updated")
    return 0


# --------------------------------------------------------------------------
# site.json commands
# --------------------------------------------------------------------------

PATH_RE = re.compile(r"^[A-Za-z_][\w-]*(\.[A-Za-z_][\w-]*)*$")


def pairs_from_args(argv):
    """Accept both `key=value` and `key value` forms, mixed freely."""
    out, i = [], 0
    while i < len(argv):
        head, sep, tail = argv[i].partition("=")
        if sep and PATH_RE.match(head):
            out.append((head, tail))
            i += 1
            continue
        if not PATH_RE.match(argv[i]):
            die("%r is not a field path (expected e.g. role or photo.src)" % argv[i])
        if i + 1 >= len(argv):
            die("no value given for %r" % argv[i])
        out.append((argv[i], argv[i + 1]))
        i += 2
    return out


def cmd_set(args):
    site = load_json(SITE_JSON)
    for path, raw in pairs_from_args(args.assignments):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        node, parts = site, path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                die("%s is not an object" % path)

        node[parts[-1]] = value
        print("%s = %s" % (path, json.dumps(value, ensure_ascii=False)))
    save_json(SITE_JSON, site)
    return maybe_build(args)


def cmd_about(args):
    site = load_json(SITE_JSON)
    paras = site.setdefault("about", [])
    if args.about_cmd == "list":
        for idx, para in enumerate(paras, 1):
            print("%d. %s" % (idx, para))
        return 0
    if args.about_cmd == "add":
        paras.append(args.text)
    elif args.about_cmd == "set":
        if not 1 <= args.n <= len(paras):
            die("paragraph %d does not exist (have %d)" % (args.n, len(paras)))
        paras[args.n - 1] = args.text
    elif args.about_cmd == "rm":
        if not 1 <= args.n <= len(paras):
            die("paragraph %d does not exist (have %d)" % (args.n, len(paras)))
        paras.pop(args.n - 1)
    save_json(SITE_JSON, site)
    return maybe_build(args)


def cmd_icons(args):
    print("built-in icon names:")
    for name in sorted(ICONS):
        print("  %s" % name)
    print('\nfor anything else, put raw markup in the entry: {"svg": "<svg ...>...</svg>", ...}')
    return 0


# --------------------------------------------------------------------------
# check / serve
# --------------------------------------------------------------------------

def cmd_check(args):
    site = load_json(SITE_JSON)
    doc = load_json(PUBS_JSON)
    pubs = doc.get("publications", [])
    problems, notes = [], []

    seen = set()
    for idx, pub in enumerate(pubs, 1):
        where = "publication #%d" % idx
        key = pub.get("key")
        if not key:
            problems.append("%s has no 'key'" % where)
            continue
        where = "'%s'" % key
        if key in seen:
            problems.append("%s: duplicate key" % where)
        seen.add(key)
        if not pub.get("title"):
            problems.append("%s: missing title" % where)
        if not pub.get("authors"):
            problems.append("%s: missing authors" % where)
        if not pub.get("venue"):
            notes.append("%s: no venue" % where)

        path = bib_path(key)
        if not os.path.exists(path):
            notes.append("%s: no BibTeX file (%s)" % (where, os.path.relpath(path, ROOT)))
        else:
            entries = parse_bib(read(path))
            if not entries:
                problems.append("%s: %s has no parsable entry"
                                % (where, os.path.relpath(path, ROOT)))
            elif entries[0]["key"] != key:
                problems.append("%s: BibTeX key is '%s'" % (where, entries[0]["key"]))

        aliases = {norm_name(a) for a in site.get("me_aliases", [])}
        if aliases and not any(norm_name(a) in aliases for a in pub.get("authors", [])):
            notes.append("%s: no author matches me_aliases — your name will not be bolded" % where)

        for link in pub.get("links", []):
            if not link.get("href"):
                problems.append("%s: link %r has no href" % (where, link.get("label")))

    for name in os.listdir(BIB_DIR) if os.path.isdir(BIB_DIR) else []:
        if name.endswith(".bib") and name[:-4] not in seen:
            notes.append("orphan BibTeX file: data/bib/%s" % name)

    for label, rel in (("photo", (site.get("photo") or {}).get("src")),
                       ("cv", (site.get("cv") or {}).get("href"))):
        if rel and not re.match(r"^[a-z]+:", rel) and not os.path.exists(os.path.join(ROOT, rel)):
            problems.append("%s file not found: %s" % (label, rel))

    for item in site.get("contact", []):
        if not item.get("svg") and item.get("icon") not in ICONS:
            problems.append("contact %r: unknown icon %r"
                            % (item.get("label"), item.get("icon")))

    html = read(INDEX)
    for name in REGIONS:
        if (BEGIN % name) not in html or (END % name) not in html:
            problems.append("index.html is missing the '%s' region markers" % name)

    for note in notes:
        print("note: %s" % note)
    for problem in problems:
        print("error: %s" % problem, file=sys.stderr)

    if problems:
        return 1
    print("ok — %d publication(s), no problems found" % len(pubs))
    if not args.no_build_check:
        if build_html() != html:
            print("note: index.html is out of date — run: ./scripts/site.py build")
    return 0


def cmd_serve(args):
    import http.server
    import socketserver
    import threading
    import webbrowser

    handler = http.server.SimpleHTTPRequestHandler

    class Handler(handler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=ROOT, **kw)

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, fmt, *a):
            if args.verbose:
                super().log_message(fmt, *a)

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", args.port), Handler) as httpd:
        url = "http://127.0.0.1:%d/" % args.port
        print("serving %s at %s  (Ctrl-C to stop)" % (os.path.relpath(ROOT), url))
        if args.open:
            threading.Timer(0.4, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print()
    return 0


def cmd_deploy(args):
    if subprocess.run(["git", "-C", ROOT, "diff", "--quiet"]).returncode == 0 and \
       subprocess.run(["git", "-C", ROOT, "diff", "--cached", "--quiet"]).returncode == 0:
        print("nothing to commit")
        return 0
    subprocess.check_call(["git", "-C", ROOT, "add", "-A"])
    subprocess.check_call(["git", "-C", ROOT, "commit", "-m", args.message])
    if args.push:
        subprocess.check_call(["git", "-C", ROOT, "push"])
    return 0


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------

def add_field_flags(parser):
    parser.add_argument("--title")
    parser.add_argument("--authors", help='comma- or semicolon-separated, e.g. "Ada Lovelace; Alan Turing"')
    parser.add_argument("--venue")
    parser.add_argument("--group", help="optional heading this entry sits under")
    parser.add_argument("--bib", metavar="FILE", help="BibTeX file, or - to read stdin")
    parser.add_argument("--pdf")
    parser.add_argument("--html")
    parser.add_argument("--code")
    parser.add_argument("--slides")
    parser.add_argument("--doi")
    parser.add_argument("--link", action="append", metavar="LABEL=URL",
                        help="extra link; repeatable. Empty URL removes that label.")
    parser.add_argument("--no-build", action="store_true",
                        help="only touch data/, do not regenerate index.html")


def add_position_flags(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--top", action="store_true")
    group.add_argument("--bottom", action="store_true")
    group.add_argument("--position", type=int, metavar="N", help="1-based slot")


def main(argv):
    parser = argparse.ArgumentParser(
        prog="site.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("build", help="regenerate index.html from data/")
    p.add_argument("--check", action="store_true",
                   help="exit 1 if index.html is out of date instead of writing it")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("check", help="validate data/ and index.html")
    p.add_argument("--no-build-check", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("serve", help="preview the site locally")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--open", action="store_true", help="open a browser window")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("icons", help="list built-in contact icons")
    p.set_defaults(func=cmd_icons)

    p = sub.add_parser("set", help="set a field in data/site.json, e.g. role='...'")
    p.add_argument("assignments", nargs="+", metavar="PATH VALUE",
                   help="PATH VALUE or PATH=VALUE; repeatable. "
                        "VALUE is parsed as JSON when it is valid JSON, else as a string.")
    p.add_argument("--no-build", action="store_true")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("about", help="edit the About paragraphs")
    asub = p.add_subparsers(dest="about_cmd", required=True)
    q = asub.add_parser("list"); q.set_defaults(func=cmd_about, no_build=True)
    q = asub.add_parser("add"); q.add_argument("text"); q.add_argument("--no-build", action="store_true"); q.set_defaults(func=cmd_about)
    q = asub.add_parser("set"); q.add_argument("n", type=int); q.add_argument("text"); q.add_argument("--no-build", action="store_true"); q.set_defaults(func=cmd_about)
    q = asub.add_parser("rm"); q.add_argument("n", type=int); q.add_argument("--no-build", action="store_true"); q.set_defaults(func=cmd_about)

    p = sub.add_parser("deploy", help="git add + commit (+ push) the site")
    p.add_argument("-m", "--message", default="Update site")
    p.add_argument("--push", action="store_true")
    p.set_defaults(func=cmd_deploy)

    pub = sub.add_parser("pub", help="add / edit / remove / reorder publications")
    psub = pub.add_subparsers(dest="pub_cmd", required=True)

    q = psub.add_parser("list", help="list publications in display order")
    q.set_defaults(func=cmd_pub_list)

    q = psub.add_parser("show", help="print one publication and its BibTeX")
    q.add_argument("key")
    q.set_defaults(func=cmd_pub_show)

    q = psub.add_parser("add", help="add a publication (usually from a .bib file)")
    q.add_argument("--key", help="defaults to the BibTeX citation key")
    add_field_flags(q)
    add_position_flags(q)
    q.set_defaults(func=cmd_pub_add)

    q = psub.add_parser("edit", help="change fields, links or BibTeX of a publication")
    q.add_argument("key")
    q.add_argument("--rename", metavar="NEWKEY")
    add_field_flags(q)
    q.set_defaults(func=cmd_pub_edit)

    q = psub.add_parser("rm", help="remove a publication")
    q.add_argument("key")
    q.add_argument("-y", "--yes", action="store_true", help="skip the confirmation")
    q.add_argument("--keep-bib", action="store_true")
    q.add_argument("--no-build", action="store_true")
    q.set_defaults(func=cmd_pub_rm)

    q = psub.add_parser("move", help="reorder a publication")
    q.add_argument("key")
    add_position_flags(q)
    q.add_argument("--up", action="store_true")
    q.add_argument("--down", action="store_true")
    q.add_argument("--no-build", action="store_true")
    q.set_defaults(func=cmd_pub_move)

    q = psub.add_parser("sort", help="sort the whole list")
    q.add_argument("--by", choices=["year", "title", "key"], default="year")
    q.add_argument("--ascending", action="store_true", help="oldest/A-Z first")
    q.add_argument("--no-build", action="store_true")
    q.set_defaults(func=cmd_pub_sort)

    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
