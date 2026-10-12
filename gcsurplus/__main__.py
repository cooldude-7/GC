"""Command-line interface: python -m gcsurplus <command>."""
import argparse
import csv
import json
import os
import sys
from pathlib import Path

from . import db
from .util import money, now_eastern, relative

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "gcsurplus.db"
DEFAULT_CACHE = ROOT / "data"

CATEGORIES = {
    "3700": "Agricultural machinery and equipment", "1500": "Aircraft and simulator",
    "1501": "Aircraft components, parts and instruments", "1900": "Boats and ships",
    "7600": "Books, maps and other publications", "5500": "Building materials, hardware and paint",
    "2300": "Cars and light trucks", "6800": "Chemicals and chemical products",
    "7000": "Computer equipment, parts and accessories",
    "5900": "Electrical equipment and supplies, motors, generators, transformers",
    "5800": "Electronic and communication equipment and supplies (radar, radio, tv)",
    "4200": "Fire fighting, safety and rescue equipment", "7900": "Fishing equipment",
    "8900": "Food and beverages", "9100": "Fuel, oil and lubricant",
    "5100": "Hand tools, maintenance and repair shop equipment", "2330": "Heavy equipment",
    "7200": "Household appliances, food preparation and serving equipment",
    "9800": "Jewelry, collectors items, arts and crafts, and more", "8800": "Livestock",
    "3200": "Machine tools", "2000": "Marine equipment and supplies",
    "3900": "Materiel handling equipment", "6500": "Medical, dental, scientific, lab equipment",
    "3800": "Miscellaneous equipment", "2310": "Motorcycles", "5400": "Moveable structures",
    "7700": "Musical instruments and accessories",
    "7100": "Office furniture and equipment, household furniture",
    "6400": "Personal and hygiene products", "5555": "Personal protection equipment",
    "6700": "Photographic, optical and surveying equipment and supplies",
    "4500": "Plumbing, heating, refrigeration and air conditioning equipment",
    "9600": "Precious metals", "3600": "Printing equipment",
    "2200": "Rail car and rail related equipment", "2320": "Recreational vehicles",
    "9500": "Scrap metal", "9400": "Scrap nonmetallic", "9700": "Scrap paper",
    "9300": "Scrap textiles", "2499": "Scrap vehicles to be destroyed", "1400": "Simulators",
    "7800": "Sporting goods and camping equipment",
    "8300": "Textiles, leather, fur, clothing and footwear", "2340": "Trailers",
    "2600": "Vehicle equipment, parts, accessories, tires and tubes",
}


def price_label(lot):
    if lot.get("sale_type") == "buy_now":
        qty = lot.get("available_qty")
        return f"BUY NOW {money(lot.get('purchase_amount'))}" + (f" x{qty}" if qty else "")
    cur = lot.get("current_bid") or 0
    if cur > 0:
        return f"bid {money(cur)}"
    floor = lot.get("next_min_bid") or lot.get("min_bid")
    return f"no bids, min {money(floor)}" if floor else "no bids"


def closing_label(lot, now):
    if not lot.get("closing"):
        return "no closing (buy now)" if lot.get("sale_type") == "buy_now" else "closing unknown"
    return f"closes {lot['closing'].replace('T', ' ')} ET ({relative(lot['closing'], now)})"


def print_result(lot, now, full=False, snippet=True):
    flags = []
    if lot.get("shipping_available"):
        flags.append("ships")
    if not lot.get("active", 1):
        flags.append("CLOSED")
    where = ", ".join(x for x in (lot.get("city"), lot.get("province")) if x)
    print(f"#{lot['lot_id']}  {lot.get('title') or '(untitled)'}")
    print(f"    {price_label(lot)} | {closing_label(lot, now)} | {where}" + (f" | {' '.join(flags)}" if flags else ""))
    cat = f"{lot.get('commodity_code') or ''} {lot.get('commodity') or ''}".strip()
    if cat:
        print(f"    {cat}")
    if snippet and lot.get("snippet"):
        print("    " + " ".join(lot["snippet"].split()))
    if full:
        for label, key in (("Description", "description"), ("Comments", "comments"), ("Location", "asset_location")):
            text = lot.get(key)
            if text:
                print(f"    {label}:")
                for line in text.splitlines():
                    print(f"      {line}")
    print(f"    {lot.get('url')}")
    print()


def print_lot(lot, now):
    print(f"Lot #{lot['lot_id']}: {lot.get('title')}")
    print("=" * 78)
    rows = [
        ("Sale type", "Buy Now" if lot.get("sale_type") == "buy_now" else "Open bidding (auction)"),
        ("Price", price_label(lot)),
        ("Bid increment", money(lot["bid_increment"]) if lot.get("bid_increment") else None),
        ("Last bid at", (lot.get("bid_date") or "").replace("T", " ") or None),
        ("Closing", closing_label(lot, now)),
        ("Posted", (lot.get("posted") or "").replace("T", " ") or None),
        ("Quantity", lot.get("quantity")),
        ("Shipping", "available" if lot.get("shipping_available") else "not offered / pickup only"),
        ("Category", f"{lot.get('commodity_code') or ''} {lot.get('commodity') or ''}".strip()),
        ("Location", ", ".join(x for x in (lot.get("city"), lot.get("province")) if x)),
        ("Sale / lot no.", f"{lot.get('sale_account') or ''} / {lot.get('lot_number') or lot.get('sale_lot_ref') or ''}"),
        ("Status", "active" if lot.get("active", 1) else "closed / no longer listed"),
        ("Details fetched", lot.get("detail_fetched")),
        ("URL", lot.get("url")),
    ]
    for label, value in rows:
        if value:
            print(f"{label:16} {value}")
    for label, key in (
        ("DESCRIPTION", "description"), ("ADDITIONAL COMMENTS", "comments"),
        ("ASSET LOCATION", "asset_location"), ("VIEWING", "viewing"), ("CONTACT", "contact"),
        ("DIVESTMENT OFFICER", "officer"), ("PAYMENT", "payment"), ("REMOVAL", "removal"),
        ("CLAUSES", "clauses"),
    ):
        text = lot.get(key)
        if text:
            print(f"\n{label}\n{'-' * len(label)}\n{text}")
    images = lot.get("images") or []
    if images:
        print("\nIMAGES\n------")
        for url in images:
            print(url)
    if lot.get("detail_error"):
        print(f"\nDETAIL ERROR: {lot['detail_error']}")
    print()


def cmd_crawl(args):
    from .crawl import Crawler

    crawler = Crawler(args.db, DEFAULT_CACHE, workers=args.workers, delay=args.delay,
                      log=(lambda m: None) if args.quiet else None)
    summary = crawler.run(
        sale_type=args.sale_type, max_pages=args.max_pages, category=args.category or "",
        listing_only=args.listing_only, refresh_details=args.refresh_details,
        refresh_older_than=args.refresh_older_than,
    )
    print(json.dumps(summary))
    return 0


def cmd_search(args):
    conn = db.connect(args.db)
    sale_type = {"open": "open_bidding", "buynow": "buy_now"}.get(args.type)
    results = db.search(
        conn, query=args.query, any_words=args.any, regex=args.regex, province=args.province,
        city=args.city, category=args.category, sale_type=sale_type, min_price=args.min_price,
        max_price=args.max_price, closing_within=args.closing_within, shipping=args.shipping,
        include_closed=args.include_closed, sort=args.sort, limit=args.limit,
    )
    if args.json:
        slim = []
        for r in results:
            r = dict(r)
            if not args.full:
                r.pop("raw_text", None)
            slim.append(r)
        print(json.dumps(slim, indent=2, ensure_ascii=False))
        return 0
    now = now_eastern()
    if not results:
        print("No matching lots. (Have you run `crawl`? Try fewer words, a prefix like weld*, or --any.)")
        return 1
    for lot in results:
        print_result(lot, now, full=args.full)
    print(f"{len(results)} result(s)" + (f" (showing first {args.limit})" if len(results) == args.limit else "")
          + f". Database last crawled: {db.get_meta(conn, 'last_crawl') or 'never'} ET")
    return 0


def cmd_show(args):
    conn = db.connect(args.db)
    now = now_eastern()
    missing = 0
    for lot_id in args.lot_id:
        lot = db.get_lot(conn, lot_id)
        if lot is None:
            print(f"Lot #{lot_id} is not in the database.")
            missing += 1
            continue
        if args.json:
            print(json.dumps(lot, indent=2, ensure_ascii=False))
        else:
            print_lot(lot, now)
    return 1 if missing else 0


def cmd_export(args):
    conn = db.connect(args.db)
    lots = db.all_lots(conn, include_closed=args.include_closed)
    out = open(args.out, "w", newline="", encoding="utf-8") if args.out else sys.stdout
    try:
        if args.format == "json":
            json.dump(lots, out, indent=2, ensure_ascii=False)
            out.write("\n")
        else:
            cols = [c for c in (lots[0].keys() if lots else []) if c != "raw_text"]
            writer = csv.DictWriter(out, fieldnames=cols, extrasaction="ignore")
            writer.writeheader()
            for lot in lots:
                lot = dict(lot)
                lot["images"] = " ".join(lot.get("images") or [])
                writer.writerow(lot)
    finally:
        if out is not sys.stdout:
            out.close()
    print(f"exported {len(lots)} lots", file=sys.stderr)
    return 0


def cmd_stats(args):
    conn = db.connect(args.db)
    s = db.stats(conn)
    print(f"Active lots:        {s['active']}  (details fetched for {s['with_details']}, errors {s['detail_errors']})")
    print(f"Closed/removed:     {s['inactive']}")
    print(f"Last crawl (ET):    {s['last_crawl'] or 'never'}")
    if s["last_crawl_summary"]:
        print(f"Last crawl summary: {s['last_crawl_summary']}")
    print("\nBy sale type:")
    for r in s["by_type"]:
        print(f"  {r['sale_type'] or '?':14} {r['n']}")
    print("\nBy province:")
    for r in s["by_province"]:
        print(f"  {r['province'] or '?':4} {r['n']}")
    print("\nBy category:")
    for r in s["by_category"]:
        print(f"  {r['commodity_code'] or '?':5} {r['n']:5}  {r['commodity'] or ''}")
    return 0


def cmd_categories(args):
    for code, name in sorted(CATEGORIES.items(), key=lambda kv: kv[1]):
        print(f"{code}  {name}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="gcsurplus",
        description="Crawl GCSurplus.ca into a local database and search every lot's title, "
                    "description, comments, location and contact text.",
    )
    p.add_argument("--db", default=str(DEFAULT_DB), help=f"SQLite file (default {DEFAULT_DB})")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("crawl", help="download every listing and lot description (run this first)")
    c.add_argument("--sale-type", choices=["A", "OB", "BN"], default="A", help="A=all (default), OB=open bidding, BN=buy now")
    c.add_argument("--category", help="only one commodity code, e.g. 3200 (then nothing is marked closed)")
    c.add_argument("--workers", type=int, default=3, help="parallel detail downloads (default 3)")
    c.add_argument("--delay", type=float, default=0.4, help="minimum seconds between requests (default 0.4)")
    c.add_argument("--listing-only", action="store_true", help="refresh prices/closings only, skip detail pages")
    c.add_argument("--refresh-details", action="store_true", help="re-download details for every active lot")
    c.add_argument("--refresh-older-than", type=float, metavar="DAYS", help="re-download details older than DAYS")
    c.add_argument("--max-pages", type=int, help="stop after N listing pages (testing)")
    c.add_argument("--quiet", action="store_true")
    c.set_defaults(func=cmd_crawl)

    s = sub.add_parser("search", help="full-text search over everything that was crawled")
    s.add_argument("query", nargs="?", help='words (all must match), "a phrase", weld* prefix, or FTS syntax: nixie OR counter')
    s.add_argument("--any", action="store_true", help="match lots containing any of the words instead of all")
    s.add_argument("--regex", "-r", help="case-insensitive regular expression tested against every text field")
    s.add_argument("--province", "-p", help="two-letter code, e.g. ON")
    s.add_argument("--city", help="substring of the city name")
    s.add_argument("--category", "-c", help="commodity code, see `categories`")
    s.add_argument("--type", choices=["open", "buynow"], help="open bidding only, or buy-now only")
    s.add_argument("--min-price", type=float)
    s.add_argument("--max-price", type=float, help="current bid / next minimum bid / buy-now price at most this")
    s.add_argument("--closing-within", type=float, metavar="DAYS", help="auctions closing within DAYS from now")
    s.add_argument("--shipping", action="store_true", help="only lots GCSurplus will ship")
    s.add_argument("--include-closed", action="store_true", help="also search lots no longer listed")
    s.add_argument("--sort", choices=["relevance", "closing", "price", "posted"], help="default: relevance with a query, else closing")
    s.add_argument("--limit", "-n", type=int, default=50)
    s.add_argument("--full", action="store_true", help="print description, comments and location for each hit")
    s.add_argument("--json", action="store_true", help="machine-readable output")
    s.set_defaults(func=cmd_search)

    sh = sub.add_parser("show", help="print everything known about one or more lots")
    sh.add_argument("lot_id", type=int, nargs="+")
    sh.add_argument("--json", action="store_true")
    sh.set_defaults(func=cmd_show)

    e = sub.add_parser("export", help="dump the database as JSON or CSV")
    e.add_argument("--format", choices=["json", "csv"], default="json")
    e.add_argument("--out", "-o", help="file to write (default stdout)")
    e.add_argument("--include-closed", action="store_true")
    e.set_defaults(func=cmd_export)

    st = sub.add_parser("stats", help="counts by province, category and sale type, plus last crawl time")
    st.set_defaults(func=cmd_stats)

    ca = sub.add_parser("categories", help="list commodity codes for --category")
    ca.set_defaults(func=cmd_categories)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:  # output piped into head/less and closed early
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
