# zinho02.github.io

Personal site — a single static `index.html` served by GitHub Pages.

The page itself stays hand-written, but the parts that change often are kept as
data and rendered into marked regions of `index.html` by `scripts/site.py`:

```
data/site.json           name, role, about paragraphs, CV, contact links, header animation
data/publications.json   the publication list, in display order
data/bib/<key>.bib       one BibTeX entry per publication
scripts/site.py          the only script you need
```

`scripts/site.py` only rewrites the text between `<!-- site.py:begin X -->` and
`<!-- site.py:end X -->` markers. Everything else in `index.html` — the CSS, the
animation code, the section scaffolding — is yours to edit by hand and is never
touched. It needs nothing but Python 3 (no pip install, no build step).

## Publications

Adding a paper normally means dropping in the `.bib` the publisher gave you:

```sh
./scripts/site.py pub add --bib ~/Downloads/paper.bib \
    --pdf  https://example.org/paper.pdf \
    --code https://github.com/zinho02/project
```

The citation key becomes the entry's key, and the title, author list and venue
are read out of the BibTeX — including LaTeX accents, so `Cust{\'o}dio` shows up
as *Custódio* on the page. Anything derived wrongly can be overridden on the
same command line (`--title`, `--authors "A; B; C"`, `--venue`), or later with
`pub edit`. Without `--top`/`--position N`, new entries go to the bottom.

No `.bib` handy? Paste one:

```sh
./scripts/site.py pub add --bib -        # reads stdin, Ctrl-D when done
```

Or skip BibTeX entirely — the entry just won't show a BibTeX button:

```sh
./scripts/site.py pub add --key smith2027 --title "..." --authors "Jane Smith; Matheus Saldanha" --venue "CRYPTO 2027"
```

Then:

```sh
./scripts/site.py pub list                          # display order, with keys
./scripts/site.py pub show  saldanha2026lut         # one entry + its BibTeX
./scripts/site.py pub edit  saldanha2026lut --venue "..."   # change fields
./scripts/site.py pub edit  saldanha2026lut --bib fixed.bib # replace the BibTeX
./scripts/site.py pub edit  saldanha2026lut --link "Slides=https://..."
./scripts/site.py pub edit  saldanha2026lut --link "Slides="  # drop that link
./scripts/site.py pub rm    saldanha2026lut
./scripts/site.py pub move  saldanha2026lut --top   # also --bottom --up --down --position N
./scripts/site.py pub sort  --by year               # newest first
```

Every one of these rewrites `index.html` straight away. Pass `--no-build` to
stage several changes and rebuild once at the end.

Your own name is bolded automatically: any author matching `me_aliases` in
`data/site.json` gets the `.me` style, accents and initials ignored. Add a
spelling there if a co-author list writes your name a new way.

## Everything else on the page

`data/site.json` holds the rest. Edit it directly, or:

```sh
./scripts/site.py set role "Ph.D. Candidate, Unicamp"
./scripts/site.py set page_title "Matheus Saldanha" meta_description "..."
./scripts/site.py set photo.src new_photo.jpeg cv.href CV_2027.pdf

./scripts/site.py about list         # numbered paragraphs
./scripts/site.py about set 2 "Previously, I earned my M.Sc. ..."
./scripts/site.py about add "I am on the job market."
./scripts/site.py about rm 3
```

About paragraphs are raw HTML, so inline `<a href="...">` links work as-is.

Footer icons come from a small built-in set (`./scripts/site.py icons`). For one
that isn't there, give the entry an `"svg"` key with the markup instead of
`"icon"`:

```json
{ "label": "ORCID", "href": "https://orcid.org/...", "svg": "<svg viewBox=\"0 0 24 24\">…</svg>" }
```

## Preview, check, publish

```sh
./scripts/site.py serve          # http://127.0.0.1:8000, --open launches a browser
./scripts/site.py check          # data problems, missing files, broken icons, stale build
./scripts/site.py build --check  # exit 1 if index.html is out of date (for CI / hooks)
./scripts/site.py deploy -m "Add INDOCRYPT paper" --push
```

`make help` lists the same things as shorter commands.

## Header animation

The isogeny-graph bar sizes its SVG viewBox to the element's real pixel
dimensions, so nodes stay circular and stroke weight stays even at any window
size, and it picks a column count from the viewport width to hold a constant
visual density. Tuning lives in `data/site.json` under `animation`:

| key | meaning |
| --- | --- |
| `pxPerColumn` | horizontal spacing between columns — **lower = denser** |
| `minColumns`, `maxColumns` | clamp on the column count |
| `nodeRadius`, `endNodeRadius` | vertex sizes, in pixels |
| `edgeWidth`, `walkWidth` | background and highlighted-path stroke widths |
| `verticalPadding` | keeps vertices clear of the bar's top and bottom edges |
| `branchProbability` | chance an interior column splits into two vertices |
| `tripleProbability` | ...of which this share splits into three |
| `maxCrossEdges` | edges drawn from each vertex to the next column |

Run `./scripts/site.py build` after editing; the values are injected into the
page as `window.ISO_CONFIG`. The defaults in `index.html` are used for any key
you leave out.
