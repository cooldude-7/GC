# GCSurplus search tool

Purpose: let Claude (and the user) search every lot on GCSurplus.ca by full text, not just titles.
The user lives in Oakville, Ontario and looks for machine-shop tools (TIG welders, lathes, mills),
vintage electronics (Nixie tubes inside old counters/voltmeters) and resale bargains.

## Workflow for Claude

1. `pip install -r requirements.txt` only if `import bs4, requests` fails.
2. `python3 -m gcsurplus crawl` first (about 5 minutes for ~1,000 lots; the DB in `data/` is not
   committed, so a fresh session must crawl). Re-run it to refresh prices and pick up new lots.
3. Search with `python3 -m gcsurplus search ...`; add `--json` or `--full` when you need the
   complete text. `python3 -m gcsurplus show <lot_id>` prints everything about one lot.
4. Quote the lot URL from the output when reporting a find; the user bids on the website.

Useful searches: `search "welder OR welding OR tig OR mig OR lathe OR milling" --province ON`,
`search --regex 'nixie|frequency counter|digital voltmeter'`, `search <thing> --closing-within 3`.

## Code map

`gcsurplus/http.py` HTTP client + certificate-chain fix, `parse.py` HTML to fields,
`db.py` SQLite + FTS5 + search, `crawl.py` orchestration, `__main__.py` CLI.
Run `python3 -m gcsurplus --help`. No test suite; verify changes with
`python3 -m gcsurplus --db data/test.db crawl --max-pages 1` then a search.
