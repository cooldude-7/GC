"""HTML parsing for GCSurplus listing pages and lot detail pages."""
import re

from bs4 import BeautifulSoup

from .util import lot_url

try:
    import lxml  # noqa: F401

    PARSER = "lxml"
except ImportError:  # pragma: no cover
    PARSER = "html.parser"

_MONTHS = {}
for _i, _names in enumerate(
    zip(
        ["january", "february", "march", "april", "may", "june", "july", "august",
         "september", "october", "november", "december"],
        ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
         "septembre", "octobre", "novembre", "décembre"],
    ),
    1,
):
    for _n in _names:
        _MONTHS[_n] = _i

DATE_RE = re.compile(
    r"(\d{1,2})-([A-Za-zéû]+)-(\d{4})(?:\s*@\s*(\d{1,2})\s*:\s*(\d{2})\s*([ap])\.?\s*m\.?)?",
    re.I,
)
RANGE_RE = re.compile(r"\(\s*(\d+)\s*-\s*(\d+)\s+of\s+([\d,]+)\s*\)")
BLOCK_TAGS = ["p", "div", "dt", "dd", "tr", "h1", "h2", "h3", "h4", "ol", "ul",
              "table", "summary", "details", "section"]


def soup(markup):
    return BeautifulSoup(markup, PARSER)


def clean(text):
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_date(text):
    """'12-October-2026 @ 5:30 a.m.' -> '2026-10-12T05:30' (site times are Eastern)."""
    if not text:
        return None
    m = DATE_RE.search(text)
    if not m:
        return None
    day, mon, year, hh, mm, ap = m.groups()
    month = _MONTHS.get(mon.lower())
    if not month:
        return None
    hour = int(hh) if hh else 0
    minute = int(mm) if mm else 0
    if ap:
        if ap.lower() == "p" and hour != 12:
            hour += 12
        elif ap.lower() == "a" and hour == 12:
            hour = 0
    try:
        return f"{int(year):04d}-{month:02d}-{int(day):02d}T{hour:02d}:{minute:02d}"
    except ValueError:
        return None


def parse_money(text):
    if not text:
        return None
    m = re.search(r"\$\s*(-?[\d,]*\d(?:\.\d+)?)", text) or re.search(r"(-?[\d,]*\d(?:\.\d+)?)", text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _int(text):
    if not text:
        return None
    m = re.search(r"\d+", text.replace(",", ""))
    return int(m.group(0)) if m else None


def html_to_text(markup):
    """Convert an HTML fragment to readable plain text, keeping bullets and line breaks."""
    s = soup(markup)
    for t in s(["script", "style"]):
        t.decompose()
    for br in s.find_all("br"):
        br.replace_with("\n")
    for li in s.find_all("li"):
        li.insert(0, "\n- ")
    for tag in s.find_all(BLOCK_TAGS):
        tag.append("\n")
    text = s.get_text().replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _split_location(text):
    text = clean(text)
    if "," in text:
        city, prov = text.rsplit(",", 1)
        return clean(city), clean(prov).upper()
    return text, ""


def _abs(url):
    if not url:
        return None
    if url.startswith("http"):
        return url
    return "https://gcsurplus.ca/" + url.lstrip("/")


def _dl_pairs(section, keep_lines=False):
    """Map 'Label' -> value text for every <dt>/<dd> pair inside a section."""
    pairs = {}
    if section is None:
        return pairs
    for dt in section.find_all("dt"):
        key = clean(dt.get_text(" ")).rstrip(":").strip()
        dd = dt.find_next_sibling("dd")
        if not key or dd is None:
            continue
        pairs[key] = html_to_text(str(dd)) if keep_lines else clean(dd.get_text(" "))
    return pairs


def parse_listing(markup):
    """Return (lots, (first, last, total)) for one search-results page."""
    s = soup(markup)
    rng = None
    m = RANGE_RE.search(s.get_text(" "))
    if m:
        rng = (int(m.group(1)), int(m.group(2)), int(m.group(3).replace(",", "")))
    lots = []
    table = s.find(id="srchResultData")
    body = table.find("tbody") if table is not None else None
    if body is None:
        return lots, rng
    for tr in body.find_all("tr", recursive=False):
        info = tr.find("td", attrs={"headers": "itemInfo"})
        if info is None:
            continue
        link = info.find("a", href=re.compile(r"lcn=\d+"))
        if link is None:
            continue
        href = link.get("href", "")
        lot_id = int(re.search(r"lcn=(\d+)", href).group(1))
        sm = re.search(r"scn=(\d+)", href)
        sale_id = int(sm.group(1)) if sm else None
        for sp in link.find_all("span", class_="hideIt"):
            sp.decompose()
        title = clean(link.get_text(" "))

        sale_type = "open_bidding"
        fields = {}
        dl = info.find("dl")
        if dl is not None:
            icon = dl.find("img")
            if icon is not None:
                label = ((icon.get("alt") or "") + " " + (icon.get("title") or "")).lower()
                if "buy now" in label or "acheter" in label:
                    sale_type = "buy_now"
            fields = _dl_pairs(dl)

        cur = fields.get("Current Bid (CAD)")
        city, province = _split_location(fields.get("Location", ""))
        comm = fields.get("Commodity", "")
        cm = re.match(r"(\d{3,5})\s*-\s*(.*)", comm)
        pic = tr.find("td", attrs={"headers": "itemPic"})
        img = pic.find("img") if pic is not None else None

        lots.append(
            {
                "lot_id": lot_id,
                "sale_id": sale_id,
                "title": title,
                "sale_type": sale_type,
                "current_bid": parse_money(cur) if cur else None,
                "bid_date": parse_date(cur) if cur else None,
                "purchase_amount": parse_money(fields.get("Purchase amount")),
                "available_qty": _int(fields.get("Available Quantity")),
                "shipping_available": 1 if "Shipping" in fields else 0,
                "posted": parse_date(fields.get("Posted")),
                "closing": parse_date(fields.get("Closing")),
                "closing_text": fields.get("Closing"),
                "commodity_code": cm.group(1) if cm else None,
                "commodity": clean(cm.group(2)) if cm else (comm or None),
                "city": city,
                "province": province,
                "sale_lot_ref": fields.get("Sale account"),
                "thumbnail": _abs(img.get("src")) if img is not None else None,
                "url": lot_url(sale_id, lot_id),
            }
        )
    return lots, rng


def parse_detail(markup):
    """Extract every field from a lot detail page (the five tabs plus the bid panel)."""
    s = soup(markup)
    info = s.find(id="lot-info")
    if info is None:
        raise ValueError("page has no lot details (lot closed/removed, or page layout changed)")

    details = _dl_pairs(info.find(id="tabLotDetails"), keep_lines=True)
    contacts = _dl_pairs(info.find(id="tabLocContacts"), keep_lines=True)
    payment = _dl_pairs(info.find(id="tabPmtRemoval"), keep_lines=True)

    comment_span = info.find(id="itemCmntId")
    description = html_to_text(str(comment_span)) if comment_span is not None else details.get("Description", "")

    clauses = []
    tab = info.find(id="tabClauses")
    if tab is not None:
        for tr in tab.find_all("tr"):
            cells = [clean(td.get_text(" ")) for td in tr.find_all("td")]
            if len(cells) >= 2 and cells[0]:
                clauses.append(f"{cells[0]}: {cells[1]}")

    panel = s.find(id="bidPanelId")
    panel_text = html_to_text(str(panel)) if panel is not None else ""
    all_text = panel_text + "\n" + html_to_text(str(info))

    m_next = re.search(r"Next minimum bid:\s*\$?\s*([\d,]+(?:\.\d+)?)\s*\(Bid increments?\s*=\s*\$?\s*([\d,]+(?:\.\d+)?)", panel_text)
    m_cur = re.search(r"Current Bid \(CAD\):\s*\$?\s*([\d,]+(?:\.\d+)?)", panel_text)
    m_buy = re.search(r"Purchase amount:\s*\$?\s*([\d,]+(?:\.\d+)?)", panel_text)
    m_qty = re.search(r"Available Quantity:\s*(\d+)", panel_text)

    shipping = None
    if re.search(r"shipping is (?:not )?available", all_text, re.I):
        shipping = 0 if re.search(r"shipping is not available", all_text, re.I) else 1

    images = []
    for img in s.find_all("img", class_="newViewer"):
        src = _abs(img.get("src"))
        if src and src not in images:
            images.append(src)

    tab_text = "\n".join(
        html_to_text(str(x)) for x in (info.find(id="tabLotDetails"), info.find(id="tabLocContacts")) if x is not None
    )

    return {
        "item": details.get("Item"),
        "min_bid": parse_money(details.get("Minimum bid")),
        "next_min_bid": float(m_next.group(1).replace(",", "")) if m_next else None,
        "bid_increment": float(m_next.group(2).replace(",", "")) if m_next else None,
        "current_bid": float(m_cur.group(1).replace(",", "")) if m_cur else None,
        "purchase_amount": float(m_buy.group(1).replace(",", "")) if m_buy else None,
        "available_qty": int(m_qty.group(1)) if m_qty else None,
        "closing": parse_date(details.get("Closing date")),
        "closing_text": clean(details.get("Closing date")) or None,
        "quantity": clean(details.get("Quantity")) or None,
        "description": description,
        "comments": details.get("Additional comments"),
        "asset_location": contacts.get("Location of asset(s)"),
        "viewing": contacts.get("Viewing"),
        "contact": contacts.get("Contact"),
        "officer": contacts.get("Divestment Officer"),
        "sale_account": clean(contacts.get("Sale account")) or None,
        "lot_number": clean(contacts.get("Lot number")) or None,
        "payment": payment.get("Payment"),
        "removal": payment.get("Removal"),
        "clauses": "\n".join(clauses),
        "images": images,
        "shipping_available": shipping,
        "raw_text": tab_text,
    }
