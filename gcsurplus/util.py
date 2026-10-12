"""Small shared helpers (time handling, formatting)."""
from datetime import datetime, timedelta

ISO = "%Y-%m-%dT%H:%M"
BASE_URL = "https://gcsurplus.ca/"


def eastern_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("America/Toronto")
    except Exception:  # pragma: no cover - missing tzdata on some systems
        return None


def now_eastern():
    """Current wall-clock time in Eastern time, naive (GCSurplus lists all times in ET)."""
    tz = eastern_tz()
    if tz is None:
        return datetime.now()
    return datetime.now(tz).replace(tzinfo=None)


def now_iso():
    return now_eastern().strftime(ISO)


def parse_iso(text):
    if not text:
        return None
    try:
        return datetime.strptime(text, ISO)
    except ValueError:
        return None


def relative(closing_iso, now=None):
    """'in 3d 4h', 'in 25m', or 'closed'."""
    dt = parse_iso(closing_iso)
    if dt is None:
        return ""
    now = now or now_eastern()
    delta = dt - now
    if delta <= timedelta(0):
        return "closed"
    secs = int(delta.total_seconds())
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return f"in {days}d {hours}h"
    if hours:
        return f"in {hours}h {mins:02d}m"
    return f"in {mins}m"


def money(value):
    if value is None:
        return "-"
    return f"${value:,.2f}"


def lot_url(sale_id, lot_id, sale_type="A"):
    return (
        f"{BASE_URL}mn-eng.cfm?snc=wfsav&sc=enc-bid&scn={sale_id}&lcn={lot_id}"
        f"&lct=L&srchtype=&lci=&str=1&lotnf=1&frmsr=1&sf=ferm-clos&saleType={sale_type}"
    )
