"""SQLite storage with an FTS5 full-text index over every text field of every lot."""
import json
import re
import shlex
import sqlite3
from datetime import timedelta
from pathlib import Path

from .util import ISO, now_eastern

FTS_COLS = [
    "title", "description", "comments", "commodity", "city", "province", "asset_location",
    "contact", "officer", "sale_account", "lot_number", "sale_lot_ref", "clauses", "raw_text",
]
LISTING_COLS = [
    "sale_id", "title", "sale_type", "current_bid", "bid_date", "purchase_amount", "available_qty",
    "shipping_available", "posted", "closing", "closing_text", "commodity_code", "commodity",
    "city", "province", "sale_lot_ref", "thumbnail", "url",
]
DETAIL_COLS = [
    "min_bid", "next_min_bid", "bid_increment", "quantity", "description", "comments",
    "asset_location", "viewing", "contact", "officer", "sale_account", "lot_number", "payment",
    "removal", "clauses", "images", "raw_text",
]
TEXT_FIELDS_FOR_REGEX = [
    "title", "description", "comments", "raw_text", "commodity", "city", "province",
    "asset_location", "viewing", "contact", "officer", "sale_account", "lot_number",
    "sale_lot_ref", "clauses", "quantity",
]

PRICE_SQL = (
    "CASE WHEN l.sale_type = 'buy_now' THEN l.purchase_amount "
    "WHEN COALESCE(l.current_bid, 0) > 0 THEN l.current_bid "
    "ELSE COALESCE(l.next_min_bid, l.min_bid) END"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS lots (
  lot_id INTEGER PRIMARY KEY,
  sale_id INTEGER,
  title TEXT,
  sale_type TEXT,
  current_bid REAL,
  bid_date TEXT,
  purchase_amount REAL,
  available_qty INTEGER,
  min_bid REAL,
  next_min_bid REAL,
  bid_increment REAL,
  quantity TEXT,
  shipping_available INTEGER,
  posted TEXT,
  closing TEXT,
  closing_text TEXT,
  commodity_code TEXT,
  commodity TEXT,
  city TEXT,
  province TEXT,
  sale_account TEXT,
  lot_number TEXT,
  sale_lot_ref TEXT,
  description TEXT,
  comments TEXT,
  asset_location TEXT,
  viewing TEXT,
  contact TEXT,
  officer TEXT,
  payment TEXT,
  removal TEXT,
  clauses TEXT,
  images TEXT,
  thumbnail TEXT,
  url TEXT,
  raw_text TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  first_seen TEXT,
  last_seen TEXT,
  detail_fetched TEXT,
  detail_error TEXT
);
CREATE INDEX IF NOT EXISTS lots_closing ON lots(active, closing);
CREATE INDEX IF NOT EXISTS lots_province ON lots(province);
CREATE INDEX IF NOT EXISTS lots_commodity ON lots(commodity_code);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _fts_schema(tokenizer):
    cols = ", ".join(FTS_COLS)
    new = ", ".join("new." + c for c in FTS_COLS)
    old = ", ".join("old." + c for c in FTS_COLS)
    return f"""
CREATE VIRTUAL TABLE IF NOT EXISTS lots_fts USING fts5(
  {cols}, content='lots', content_rowid='lot_id', tokenize='{tokenizer}'
);
CREATE TRIGGER IF NOT EXISTS lots_ai AFTER INSERT ON lots BEGIN
  INSERT INTO lots_fts(rowid, {cols}) VALUES (new.lot_id, {new});
END;
CREATE TRIGGER IF NOT EXISTS lots_ad AFTER DELETE ON lots BEGIN
  INSERT INTO lots_fts(lots_fts, rowid, {cols}) VALUES ('delete', old.lot_id, {old});
END;
CREATE TRIGGER IF NOT EXISTS lots_au AFTER UPDATE ON lots BEGIN
  INSERT INTO lots_fts(lots_fts, rowid, {cols}) VALUES ('delete', old.lot_id, {old});
  INSERT INTO lots_fts(rowid, {cols}) VALUES (new.lot_id, {new});
END;
"""


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    try:
        conn.executescript(_fts_schema("unicode61 remove_diacritics 2"))
    except sqlite3.OperationalError:
        conn.executescript(_fts_schema("unicode61 remove_diacritics 1"))
    conn.commit()
    return conn


def set_meta(conn, key, value):
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def upsert_listing(conn, lot, stamp):
    cols = ["lot_id"] + LISTING_COLS + ["first_seen", "last_seen", "active"]
    values = [lot["lot_id"]] + [lot.get(c) for c in LISTING_COLS] + [stamp, stamp, 1]
    updates = ", ".join(f"{c} = excluded.{c}" for c in LISTING_COLS) + ", last_seen = excluded.last_seen, active = 1"
    conn.execute(
        f"INSERT INTO lots ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT(lot_id) DO UPDATE SET {updates}",
        values,
    )


def update_detail(conn, lot_id, detail, stamp):
    sets = {c: detail.get(c) for c in DETAIL_COLS}
    sets["images"] = json.dumps(detail.get("images") or [])
    sets["detail_fetched"] = stamp
    sets["detail_error"] = None
    assignments = ", ".join(f"{c} = ?" for c in sets)
    params = list(sets.values())
    # Fields the detail page knows better than the listing, but only when it actually had them.
    for col in ("closing", "closing_text", "current_bid", "purchase_amount", "available_qty", "shipping_available"):
        if detail.get(col) is not None:
            assignments += f", {col} = ?"
            params.append(detail[col])
    if detail.get("item"):  # fill the title only if the listing somehow lacked one
        assignments += ", title = COALESCE(NULLIF(title, ''), ?)"
        params.append(detail["item"])
    conn.execute(f"UPDATE lots SET {assignments} WHERE lot_id = ?", params + [lot_id])


def record_detail_error(conn, lot_id, message, stamp):
    conn.execute("UPDATE lots SET detail_error = ? WHERE lot_id = ?", (f"{stamp} {message}"[:500], lot_id))


def lots_needing_detail(conn, refresh_all=False, older_than_days=None):
    where = "active = 1"
    params = []
    if not refresh_all:
        conds = ["detail_fetched IS NULL"]
        if older_than_days is not None:
            cutoff = (now_eastern() - timedelta(days=older_than_days)).strftime(ISO)
            conds.append("detail_fetched < ?")
            params.append(cutoff)
        where += " AND (" + " OR ".join(conds) + ")"
    rows = conn.execute(f"SELECT lot_id, sale_id FROM lots WHERE {where} ORDER BY closing IS NULL, closing", params).fetchall()
    return [(r["lot_id"], r["sale_id"]) for r in rows]


def deactivate_missing(conn, seen_ids):
    """Mark lots that vanished from the listing as inactive (closed/sold).

    A lot that is missing but whose closing time is still in the future is kept active:
    listing pages shift while they are being fetched, so one miss is not proof it is gone.
    """
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS seen_ids (lot_id INTEGER PRIMARY KEY)")
    conn.execute("DELETE FROM seen_ids")
    conn.executemany("INSERT OR IGNORE INTO seen_ids(lot_id) VALUES (?)", [(i,) for i in seen_ids])
    now = now_eastern().strftime(ISO)
    cur = conn.execute(
        "UPDATE lots SET active = 0 WHERE active = 1 AND lot_id NOT IN (SELECT lot_id FROM seen_ids) "
        "AND (closing IS NULL OR closing < ?)",
        (now,),
    )
    conn.execute("DELETE FROM seen_ids")
    return cur.rowcount


def build_match(query, any_words=False):
    """Turn a human query into FTS5 syntax.

    Plain words are quoted (so punctuation is safe) and AND-ed; a trailing * keeps prefix
    matching; quoted phrases are kept together. If the query already uses FTS operators
    (AND / OR / NOT / NEAR) it is passed through untouched.
    """
    q = query.strip()
    if re.search(r"\b(AND|OR|NOT)\b|\bNEAR\(", q):
        return q
    try:
        tokens = shlex.split(q)
    except ValueError:
        tokens = q.split()
    parts = []
    for tok in tokens:
        prefix = tok.endswith("*")
        tok = tok.rstrip("*").replace('"', "")
        if not tok:
            continue
        parts.append(f'"{tok}"' + ("*" if prefix else ""))
    return (" OR " if any_words else " AND ").join(parts)


def row_to_dict(row):
    d = dict(row)
    if isinstance(d.get("images"), str):
        try:
            d["images"] = json.loads(d["images"])
        except ValueError:
            d["images"] = []
    return d


def search(
    conn,
    query=None,
    any_words=False,
    regex=None,
    province=None,
    city=None,
    category=None,
    sale_type=None,
    min_price=None,
    max_price=None,
    closing_within=None,
    shipping=False,
    include_closed=False,
    sort=None,
    limit=50,
):
    where, params = [], []
    select = f"l.*, {PRICE_SQL} AS price"
    source = "lots l"
    if query:
        source = "lots_fts JOIN lots l ON l.lot_id = lots_fts.rowid"
        where.append("lots_fts MATCH ?")
        params.append(build_match(query, any_words))
        select += ", snippet(lots_fts, -1, '[', ']', ' … ', 16) AS snippet, bm25(lots_fts) AS rank"
    else:
        select += ", NULL AS snippet, 0 AS rank"
    if not include_closed:
        where.append("l.active = 1")
    if province:
        where.append("UPPER(l.province) = ?")
        params.append(province.strip().upper())
    if city:
        where.append("LOWER(l.city) LIKE ?")
        params.append(f"%{city.strip().lower()}%")
    if category:
        where.append("l.commodity_code = ?")
        params.append(str(category))
    if sale_type:
        where.append("l.sale_type = ?")
        params.append(sale_type)
    if shipping:
        where.append("l.shipping_available = 1")
    if min_price is not None:
        where.append(f"{PRICE_SQL} >= ?")
        params.append(min_price)
    if max_price is not None:
        where.append(f"{PRICE_SQL} <= ?")
        params.append(max_price)
    if closing_within is not None:
        now = now_eastern()
        where.append("l.closing IS NOT NULL AND l.closing >= ? AND l.closing <= ?")
        params += [now.strftime(ISO), (now + timedelta(days=closing_within)).strftime(ISO)]
    orders = {
        "closing": "l.closing IS NULL, l.closing ASC",
        "price": "price IS NULL, price ASC",
        "posted": "l.posted DESC",
        "relevance": "rank ASC, l.closing IS NULL, l.closing ASC",
    }
    order = orders[sort or ("relevance" if query else "closing")]
    sql = f"SELECT {select} FROM {source}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order}"
    if not regex and limit:
        sql += f" LIMIT {int(limit)}"
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        if query and "fts5" in str(e).lower():
            # The query used characters FTS5 could not parse: fall back to plain quoted words.
            params[0] = " AND ".join(f'"{t}"' for t in re.findall(r"[\w'-]+", query))
            rows = conn.execute(sql, params).fetchall()
        else:
            raise
    results = [row_to_dict(r) for r in rows]
    if regex:
        rx = re.compile(regex, re.I | re.M)
        results = [r for r in results if rx.search("\n".join(str(r.get(f) or "") for f in TEXT_FIELDS_FOR_REGEX))]
        if limit:
            results = results[: int(limit)]
    return results


def get_lot(conn, lot_id):
    row = conn.execute(f"SELECT l.*, {PRICE_SQL} AS price FROM lots l WHERE lot_id = ?", (lot_id,)).fetchone()
    return row_to_dict(row) if row else None


def stats(conn):
    out = {
        "active": conn.execute("SELECT COUNT(*) FROM lots WHERE active = 1").fetchone()[0],
        "inactive": conn.execute("SELECT COUNT(*) FROM lots WHERE active = 0").fetchone()[0],
        "with_details": conn.execute("SELECT COUNT(*) FROM lots WHERE active = 1 AND detail_fetched IS NOT NULL").fetchone()[0],
        "detail_errors": conn.execute("SELECT COUNT(*) FROM lots WHERE active = 1 AND detail_error IS NOT NULL").fetchone()[0],
        "last_crawl": get_meta(conn, "last_crawl"),
        "last_crawl_summary": get_meta(conn, "last_crawl_summary"),
        "by_province": conn.execute(
            "SELECT province, COUNT(*) AS n FROM lots WHERE active = 1 GROUP BY province ORDER BY n DESC"
        ).fetchall(),
        "by_category": conn.execute(
            "SELECT commodity_code, commodity, COUNT(*) AS n FROM lots WHERE active = 1 GROUP BY commodity_code ORDER BY n DESC"
        ).fetchall(),
        "by_type": conn.execute("SELECT sale_type, COUNT(*) AS n FROM lots WHERE active = 1 GROUP BY sale_type").fetchall(),
    }
    return out


def all_lots(conn, include_closed=False):
    where = "" if include_closed else " WHERE active = 1"
    rows = conn.execute(f"SELECT l.*, {PRICE_SQL} AS price FROM lots l{where} ORDER BY closing IS NULL, closing").fetchall()
    return [row_to_dict(r) for r in rows]
