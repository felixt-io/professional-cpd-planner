import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_PATH = os.path.join(ROOT_DIR, "docs", "data.json")
CONFIG_PATH = os.path.join(ROOT_DIR, "config", "site.json")
TAXONOMY_PATH = os.path.join(ROOT_DIR, "docs", "taxonomy.json")


@dataclass
class Event:
    event_id: str
    code: str
    name: str
    event_type: str
    event_date_raw: str
    event_time_raw: str
    closing_date_raw: str
    division: str
    organizer: str
    venue: str
    language: str
    priority: str
    fee_text: str
    payment_text: str
    payment_hkd: Optional[int]
    details_text: str
    remarks_text: str
    status: str
    url: str
    categories: List[str]
    event_date_iso: Optional[str]
    start_local: Optional[str]
    end_local: Optional[str]
    is_upcoming: bool


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=True, indent=2, sort_keys=True)
    os.replace(tmp, path)


def http_get(session: requests.Session, url: str) -> str:
    r = session.get(url, timeout=60, headers={"User-Agent": "cpd-planner/1.0"})
    r.raise_for_status()
    return r.text


def http_get_direct(url: str) -> str:
    r = requests.get(url, timeout=60, headers={"User-Agent": "cpd-planner/1.0"})
    r.raise_for_status()
    return r.text


def normalize_status(text: str) -> str:
    t = text.strip().lower()
    if "full" in t:
        return "Full"
    if "closed" in t:
        return "Closed"
    if "open" in t:
        return "Open"
    return text.strip() or "Unknown"


def extract_field_block(text: str, label: str, next_labels: List[str]) -> str:
    lab = re.escape(label)
    if next_labels:
        nxt = "|".join(re.escape(x) for x in next_labels)
        regex = rf"(?m)^\s*{lab}\s*:\s*(.*?)(?=^\s*(?:{nxt})\s*:|\Z)"
    else:
        regex = rf"(?m)^\s*{lab}\s*:\s*(.*)\Z"
    pattern = re.compile(regex, re.IGNORECASE | re.DOTALL)
    m = pattern.search(text)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()


def parse_fields(flat: str, labels: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for i, label in enumerate(labels):
        next_labels = labels[i + 1 :]
        out[label] = extract_field_block(flat, label, next_labels)
    return out


def parse_event_date(raw: str) -> Optional[datetime]:
    raw = raw.strip()
    if not raw:
        return None
    if " to " in raw.lower() or "please refer" in raw.lower():
        return None
    for fmt in ("%Y-%m-%d", "%d %b, %Y", "%d %B, %Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def parse_time_range(raw: str) -> Optional[Tuple[str, str]]:
    if not raw:
        return None
    cleaned = raw.replace("–", "-")
    cleaned = cleaned.replace("to", "-")
    cleaned = re.sub(r"\s+", " ", cleaned)
    pat = re.compile(
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*-\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
        re.IGNORECASE,
    )
    m = pat.search(cleaned)
    if not m:
        return None
    sh, sm, sap, eh, em, eap = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), m.group(6)
    sm = sm or "00"
    em = em or "00"
    start = to_24h(int(sh), int(sm), sap)
    end = to_24h(int(eh), int(em), eap)
    return start, end


def to_24h(hour: int, minute: int, ampm: str) -> str:
    ap = ampm.lower()
    if ap == "pm" and hour != 12:
        hour += 12
    if ap == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}:00"


def format_offset(config: dict) -> str:
    offset = int(config.get("timezone_offset_hours", 0))
    sign = "+" if offset >= 0 else "-"
    return f"{sign}{abs(offset):02d}:00"


def parse_payment_hkd(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"\bHKD\s*([0-9]+)\b", text, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    m = re.search(r"HK\$\s*([0-9]+)", text, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def fee_bucket(payment_hkd: Optional[int], fee_text: str) -> str:
    if payment_hkd is not None:
        if payment_hkd == 0:
            return "Free"
        return "Paid"
    if fee_text:
        if "free" in fee_text.lower():
            return "Free"
        if "hk$" in fee_text.lower() or "hkd" in fee_text.lower():
            return "Paid"
    return "Others"


def load_taxonomy() -> List[dict]:
    if not os.path.exists(TAXONOMY_PATH):
        return []
    return load_json(TAXONOMY_PATH).get("categories", [])


def categorize_event(text: str, taxonomy: List[dict]) -> List[str]:
    text_l = text.lower()
    matches: List[str] = []
    for cat in taxonomy:
        name = cat.get("name", "").strip()
        if not name:
            continue
        keywords = cat.get("keywords", [])
        for kw in keywords:
            if kw and kw.lower() in text_l:
                matches.append(name)
                break
    if not matches:
        return ["Other / General"]
    return matches


def parse_listing_page(html: str, config: dict) -> List[Tuple[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    rows: List[Tuple[str, str]] = []
    row_selector = config.get("listing_row_selector", "table tr")
    link_selector = config.get("listing_link_selector", "a[href]")
    status_selector = config.get("listing_status_selector", "td:last-child")
    id_regex = re.compile(config.get("id_regex", r"(\\d+)"))

    for row in soup.select(row_selector):
        link = row.select_one(link_selector)
        if not link:
            continue
        href = link.get("href", "")
        m = id_regex.search(href)
        if not m:
            continue
        status_cell = row.select_one(status_selector) if status_selector else None
        status_text = status_cell.get_text(" ", strip=True) if status_cell else ""
        rows.append((m.group(1), normalize_status(status_text)))
    return rows


def parse_detail(html: str, event_id: str, status: str, taxonomy: List[dict], config: dict) -> Event:
    soup = BeautifulSoup(html, "lxml")
    flat = soup.get_text("\n", strip=True)
    flat = re.sub(r"[ \t]+", " ", flat)

    labels = config.get("detail_labels", [])
    fields = parse_fields(flat, labels)

    def pick(name: str) -> str:
        return fields.get(name, "").strip()

    code = pick("Code")
    name = pick("Event Name")
    event_type = pick("Event Type")
    event_date_raw = pick("Event Date")
    event_time_raw = pick("Event Time")
    closing_date_raw = pick("Registration Closing Date")
    venue = pick("Venue")
    division = pick("Division")
    organizer = pick("Organizer")
    fee_text = pick("Fee")
    priority = pick("Priority")
    language = pick("Language")
    details_text = pick("Details")
    remarks_text = pick("Remarks")
    payment_text = pick("Payment")

    payment_hkd = parse_payment_hkd(payment_text) or parse_payment_hkd(fee_text)

    date_obj = parse_event_date(event_date_raw)
    time_range = parse_time_range(event_time_raw)
    event_date_iso = date_obj.strftime("%Y-%m-%d") if date_obj else None

    start_local = None
    end_local = None
    if date_obj and time_range:
        start_time, end_time = time_range
        offset_str = format_offset(config)
        start_local = f"{event_date_iso}T{start_time}{offset_str}"
        end_local = f"{event_date_iso}T{end_time}{offset_str}"

    now_local = datetime.now(get_timezone(config)).date()
    is_upcoming = False
    if date_obj:
        is_upcoming = date_obj.date() >= now_local

    text_for_tagging = "\n".join(
        [name, details_text, remarks_text, division, organizer, venue, language]
    )
    categories = categorize_event(text_for_tagging, taxonomy)

    url = config.get("detail_url_template", "").format(id=event_id)

    return Event(
        event_id=event_id,
        code=code,
        name=name,
        event_type=event_type,
        event_date_raw=event_date_raw,
        event_time_raw=event_time_raw,
        closing_date_raw=closing_date_raw,
        division=division,
        organizer=organizer,
        venue=venue,
        language=language,
        priority=priority,
        fee_text=fee_text,
        payment_text=payment_text,
        payment_hkd=payment_hkd,
        details_text=details_text,
        remarks_text=remarks_text,
        status=status,
        url=url,
        categories=categories,
        event_date_iso=event_date_iso,
        start_local=start_local,
        end_local=end_local,
        is_upcoming=is_upcoming,
    )


def fetch_detail(event_id: str, status: str, taxonomy: List[dict], config: dict) -> Event:
    html = http_get_direct(config.get("detail_url_template", "").format(id=event_id))
    return parse_detail(html, event_id, status, taxonomy, config)


def get_timezone(config: dict) -> timezone:
    offset = config.get("timezone_offset_hours", 0)
    return timezone(timedelta(hours=offset))


def main() -> int:
    config = load_json(CONFIG_PATH)
    tz = get_timezone(config)

    data = {
        "last_run_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "last_run_local": datetime.now(tz).replace(microsecond=0).isoformat(),
        "scheduled_refresh_local": "Daily at 12:00 PM",
        "source_url": config.get("listing_url_template", ""),
        "events": [],
        "errors": [],
        "taxonomy": [],
    }

    taxonomy = load_taxonomy()
    data["taxonomy"] = [
        {"id": c.get("id"), "name": c.get("name"), "rank": c.get("rank")}
        for c in taxonomy
    ]

    session = requests.Session()
    status_map: Dict[str, str] = {}

    try:
        page = 1
        max_pages = int(config.get("max_pages", 10))
        from_date = datetime.now(tz).date().isoformat()
        listing_template = config.get("listing_url_template", "")
        while page <= max_pages:
            url = listing_template.format(page=page, from_date=from_date)
            html = http_get(session, url)
            rows = parse_listing_page(html, config)
            if not rows:
                break
            new_count = 0
            for event_id, status in rows:
                if event_id not in status_map:
                    new_count += 1
                    status_map[event_id] = status
            if new_count == 0 and page > 1:
                break
            page += 1
    except Exception as e:
        data["errors"].append(f"Listing fetch/parse failed: {e!r}")

    events: List[Event] = []
    max_workers = int(config.get("max_workers", 6))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(fetch_detail, event_id, status, taxonomy, config): event_id
            for event_id, status in status_map.items()
        }
        for future in as_completed(future_map):
            event_id = future_map[future]
            try:
                ev = future.result()
                events.append(ev)
            except Exception as e:
                data["errors"].append(f"Detail fetch/parse failed for id={event_id}: {e!r}")

    for ev in events:
        data["events"].append(
            {
                "event_id": ev.event_id,
                "cpd_code": ev.code,
                "name": ev.name,
                "event_type": ev.event_type,
                "event_date": ev.event_date_raw,
                "event_date_iso": ev.event_date_iso,
                "event_time": ev.event_time_raw,
                "closing_date": ev.closing_date_raw,
                "division": ev.division,
                "organizer": ev.organizer,
                "venue": ev.venue,
                "language": ev.language,
                "priority": ev.priority,
                "fee_text": ev.fee_text,
                "payment_text": ev.payment_text,
                "payment_hkd": ev.payment_hkd,
                "fee_bucket": fee_bucket(ev.payment_hkd, ev.fee_text),
                "details": ev.details_text,
                "remarks": ev.remarks_text,
                "status": ev.status,
                "url": ev.url,
                "categories": ev.categories,
                "start_local": ev.start_local,
                "end_local": ev.end_local,
                "is_upcoming": ev.is_upcoming,
            }
        )

    save_json(DATA_PATH, data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
