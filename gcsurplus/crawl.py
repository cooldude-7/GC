"""Crawl every listing page, then every lot's detail page, into the local database."""
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import db, parse
from .http import Client
from .util import now_iso

RPP = 25  # the site caps results per page at 25


def listing_params(sale_type, start, category="", keyword=""):
    return {
        "snc": "wfsav", "vndsld": "0", "sc": "ach-shop", "lci": "", "sf": "ferm-clos", "so": "ASC",
        "srchtype": "", "hpcs": category or "", "hpsr": "", "kws": keyword or "", "jstp": "",
        "saleType": sale_type, "str": str(start), "sr": "1", "rpp": str(RPP),
    }


def detail_params(sale_id, lot_id, sale_type="A"):
    return {
        "snc": "wfsav", "sc": "enc-bid", "scn": str(sale_id), "lcn": str(lot_id), "lct": "L",
        "srchtype": "", "lci": "", "str": "1", "lotnf": "1", "frmsr": "1", "sf": "ferm-clos",
        "saleType": sale_type,
    }


class Crawler:
    def __init__(self, db_path, cache_dir, workers=3, delay=0.4, log=None):
        self.conn = db.connect(db_path)
        self.client = Client(cache_dir, delay=delay)
        self.workers = max(1, workers)
        self.log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))

    def crawl_listing(self, sale_type="A", max_pages=None, category=""):
        """Walk every results page; returns (seen_lot_ids, total_reported, complete)."""
        start, page, total, seen = 1, 0, None, []
        while True:
            page += 1
            html = self.client.get("mn-eng.cfm", params=listing_params(sale_type, start, category))
            lots, rng = parse.parse_listing(html)
            if rng:
                total = rng[2]
            stamp = now_iso()
            for lot in lots:
                db.upsert_listing(self.conn, lot, stamp)
                seen.append(lot["lot_id"])
            self.conn.commit()
            self.log(f"listing page {page}: {len(lots)} lots (records {start}-{start + len(lots) - 1} of {total})")
            if not lots or (total is not None and start + RPP > total) or (max_pages and page >= max_pages):
                break
            start += RPP
        unique = len(set(seen))
        complete = total is not None and not max_pages and not category and unique >= total - RPP
        return seen, total, complete

    def fetch_detail(self, sale_id, lot_id):
        html = self.client.get("mn-eng.cfm", params=detail_params(sale_id, lot_id))
        return parse.parse_detail(html)

    def crawl_details(self, todo):
        done, errors = 0, []
        if not todo:
            return done, errors
        self.log(f"fetching details for {len(todo)} lots with {self.workers} workers ...")
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self.fetch_detail, sale_id, lot_id): lot_id for lot_id, sale_id in todo}
            for i, fut in enumerate(as_completed(futures), 1):
                lot_id = futures[fut]
                stamp = now_iso()
                try:
                    db.update_detail(self.conn, lot_id, fut.result(), stamp)
                    done += 1
                except Exception as e:  # noqa: BLE001 - keep crawling, record the failure
                    errors.append((lot_id, str(e)))
                    db.record_detail_error(self.conn, lot_id, str(e), stamp)
                if i % 25 == 0 or i == len(todo):
                    self.conn.commit()
                    rate = i / max(time.monotonic() - started, 1e-6)
                    self.log(f"  details {i}/{len(todo)} ({done} ok, {len(errors)} failed, {rate:.1f}/s)")
        self.conn.commit()
        return done, errors

    def run(self, sale_type="A", max_pages=None, category="", listing_only=False,
            refresh_details=False, refresh_older_than=None):
        started = time.monotonic()
        seen, total, complete = self.crawl_listing(sale_type, max_pages, category)
        deactivated = 0
        if complete:
            deactivated = db.deactivate_missing(self.conn, seen)
            self.conn.commit()
            self.log(f"listing complete: {len(set(seen))} active lots, {deactivated} marked closed")
        else:
            self.log("listing was partial (page limit, category filter, or count mismatch): nothing marked closed")
        done, errors = 0, []
        if not listing_only:
            todo = db.lots_needing_detail(self.conn, refresh_all=refresh_details, older_than_days=refresh_older_than)
            done, errors = self.crawl_details(todo)
        summary = {
            "listing_lots": len(set(seen)),
            "site_total": total,
            "deactivated": deactivated,
            "details_fetched": done,
            "detail_errors": len(errors),
            "requests": self.client.requests_made,
            "seconds": round(time.monotonic() - started, 1),
        }
        db.set_meta(self.conn, "last_crawl", now_iso())
        db.set_meta(self.conn, "last_crawl_summary", str(summary))
        self.conn.commit()
        for lot_id, msg in errors[:10]:
            self.log(f"  detail error lot {lot_id}: {msg}")
        if len(errors) > 10:
            self.log(f"  ... and {len(errors) - 10} more (rerun `crawl` to retry them)")
        return summary
