# gcsurplus-search

Crawl [GCSurplus.ca](https://www.gcsurplus.ca) (the Government of Canada's surplus-sales site) into a
local SQLite database and search **everything** about every lot: title, description, additional
comments, category, city/province, asset address, contact and divestment officer, sale/lot numbers
and sale clauses. The site's own search only looks at titles and is capped at 20 characters.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate   # optional
pip install -r requirements.txt
```

Python 3.9+ with SQLite FTS5 (any normal Python build has it).

## Usage

```bash
# 1. Download every listing + every lot's detail page (~1,000 lots, about 5 minutes; re-runs only
#    fetch lots that are new, and refresh prices/closing times for the rest)
python -m gcsurplus crawl

# 2. Search (all words must match; use "quotes" for phrases, weld* for prefixes, OR for alternatives)
python -m gcsurplus search "tig welder"
python -m gcsurplus search "welder OR welding OR tig OR mig" --province ON
python -m gcsurplus search lathe --max-price 500 --closing-within 7
python -m gcsurplus search "frequency counter" --any            # any of the words
python -m gcsurplus search --regex 'nixie|IN-1[48]|B-?5870'      # regex over every text field
python -m gcsurplus search --category 3200 --sort price          # browse a category (machine tools)
python -m gcsurplus search headphones --shipping --full          # print full descriptions

# 3. Everything about one lot (description, comments, address, contacts, clauses, photos)
python -m gcsurplus show 769050

# 4. Other
python -m gcsurplus stats          # counts by province / category, last crawl time
python -m gcsurplus categories     # commodity codes for --category
python -m gcsurplus export --format csv -o lots.csv
python -m gcsurplus search ... --json   # machine-readable
```

Filters for `search`: `--province ON`, `--city ottawa`, `--category 3200`, `--type open|buynow`,
`--min-price`, `--max-price`, `--closing-within DAYS`, `--shipping`, `--include-closed`,
`--sort relevance|closing|price|posted`, `--limit N`.

"Price" means the current bid for auctions with bids, the next minimum bid for auctions with none,
or the Buy Now price. All times are Eastern, as on the site.

## How it works

* `gcsurplus/crawl.py` walks the "All sales" results (25 lots per page, the site's maximum), then
  fetches each lot page with 3 workers and a global rate limit of one request every 0.4 s.
  Lots that disappear from the listing are marked closed, not deleted, so `--include-closed` can
  still find them.
* `gcsurplus/parse.py` turns the listing rows and the five tabs of a lot page (Details, Shipping,
  Location and Contacts, Payment and Removal, Clauses) into plain fields.
* `gcsurplus/db.py` stores them in `data/gcsurplus.db` with an FTS5 index (triggers keep it in
  sync) and implements the search/filter logic.
* `gcsurplus/http.py` fixes a quirk of the site: gcsurplus.ca does not send the Entrust
  intermediate certificate that signs its leaf certificate. Browsers fetch it automatically; Python
  does not, so plain `requests` fails with "unable to get local issuer certificate". The public
  intermediate (`gcsurplus/certs/`) is appended to the normal trust store at runtime; verification
  stays fully enabled.

## Notes

* Be polite: keep `--delay` at 0.3 s or more and don't run crawls in a tight loop. A full crawl is
  about 1,100 requests.
* The database lives in `data/` and is git-ignored; just run `crawl` again on a new machine.
* Bidding still happens on the website; this tool is read-only.
