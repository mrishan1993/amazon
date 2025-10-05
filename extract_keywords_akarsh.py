#!/usr/bin/env python3
"""
check_human_like.py

Improved version of check_scrape_blocking.py that tries to mimic human browsing:
 - randomized headers (User-Agent, sec-* headers)
 - realistic Referer (Amazon search URL using mapped keyword where possible)
 - randomized request order
 - polite randomized delays and occasional long pauses
 - retries with exponential backoff + jitter on bot detection / 5xx
 - optional cloudscraper fallback and proxy support

Outputs:
 - debug_html/debug_<ASIN>.html
 - fetch_debug_summary.csv

Usage:
    python3 check_human_like.py
"""

import os
import yaml
import requests
import random
import time
import csv
import re
import math
from datetime import datetime, timezone

# ====== CONFIG ======
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRACK_YAML = os.path.join(BASE_DIR, "track_akarsh.yaml")
OUT_CSV = os.path.join(BASE_DIR, "fetch_debug_summary.csv")
DEBUG_DIR = os.path.join(BASE_DIR, "debug_html")
os.makedirs(DEBUG_DIR, exist_ok=True)

# Enable cloudscraper by installing: pip install cloudscraper
USE_CLOUDSCRAPER = os.getenv("USE_CLOUDSCRAPER", "0") == "1"
if USE_CLOUDSCRAPER:
    try:
        import cloudscraper
        cs = cloudscraper.create_scraper()
    except Exception as e:
        print("[WARN] cloudscraper enabled but import failed:", e)
        USE_CLOUDSCRAPER = False

# Optional proxy via env var: set SCRAPER_PROXY="http://user:pass@host:port"
PROXY = os.getenv("SCRAPER_PROXY")
PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None

# small pool of realistic desktop and mobile UAs
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:116.0) Gecko/20100101 Firefox/116.0",
    # a common mobile UA
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
]

ACCEPT_LANGS = ["en-IN,en;q=0.9", "en-GB,en;q=0.9,en-US;q=0.8", "en-US,en;q=0.9"]

# Bot detection markers and PDP markers
BOT_MARKERS = [
    "enter the characters you see below",
    "validatecaptcha",
    "api-services-support@amazon.com",
    "to discuss automated access to amazon data",
    "sorry! something went wrong",
    "unusual traffic from your network",
    "robot check",
    "type the characters you see in the picture",
    "amazon.com has detected unusual traffic",
    "we've detected unusual activity",
    "help us confirm you are a human",
    "service temporarily unavailable",
]
PDP_MARKERS = ["#productTitle", "feature-bullets", "/dp/"]

# Tunables
SHORT_SLEEP_LOW = 2.0
SHORT_SLEEP_HIGH = 6.0
LONG_BREAK_PROB = 0.08            # 8% chance of taking a longer break between ASINs
LONG_BREAK_MIN = 30               # seconds
LONG_BREAK_MAX = 90
MAX_RETRIES = 3                   # per ASIN for backoff
BACKOFF_BASE = 2.0                # seconds (multiplied exponentially)
SAVE_HTML_LIMIT = 200_000

# ====== Helpers ======
def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def build_asins_map(mapping):
    """Return dict asin -> list(keywords) and list of unique asins"""
    asin_to_kw = {}
    for kw, lst in mapping.items():
        if not isinstance(lst, list):
            continue
        for a in lst:
            if not isinstance(a, str) or not a.strip():
                continue
            a = a.strip()
            asin_to_kw.setdefault(a, []).append(kw)
    return asin_to_kw

def choose_referer_for_asin(asin, asin_to_kw):
    """Construct a realistic Amazon search referer using a mapped keyword where possible."""
    kws = asin_to_kw.get(asin) or []
    if kws:
        kw = random.choice(kws)
        q = re.sub(r"\s+", "+", kw.strip())
        return f"https://www.amazon.in/s?k={q}"
    # fallback generic referers (homepage or category)
    fallbacks = [
        "https://www.amazon.in/",
        "https://www.amazon.in/gp/bestsellers",
        "https://www.amazon.in/s?k=saree",
    ]
    return random.choice(fallbacks)

def random_headers(asin, asin_to_kw):
    ua = random.choice(USER_AGENTS)
    accept_lang = random.choice(ACCEPT_LANGS)
    referer = choose_referer_for_asin(asin, asin_to_kw)
    # Build additional modern browser headers
    sec_ch_ua = '"Chromium";v="120", "Not:A-Brand";v="99"'
    headers = {
        "User-Agent": ua,
        "Accept-Language": accept_lang,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": referer,
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        # browser client hints (not real but plausible)
        "Sec-CH-UA": sec_ch_ua,
        "Sec-CH-UA-Mobile": "?0" if "Mobile" not in ua else "?1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Accept-Encoding": "gzip, deflate, br",
    }
    return headers

def sniff_bot(html_lower):
    found = []
    for m in BOT_MARKERS:
        if m in html_lower:
            found.append(m)
    if re.search(r"captcha", html_lower) and "enter the characters" not in found:
        found.append("captcha")
    if re.search(r"503 service temporarily|service temporarily unavailable", html_lower):
        found.append("service_unavailable")
    return found

def has_pdp_signals(html_lower):
    for marker in PDP_MARKERS:
        if marker in html_lower:
            return True
    return False

# ====== Fetch logic with human-like behavior ======
def fetch_with_backoff(asin, session, asin_to_kw, proxies=None):
    """
    Attempt to fetch an ASIN page using randomized headers.
    On detection or 5xx, do exponential backoff with jitter and retry up to MAX_RETRIES.
    Returns a result dict similar to previous script.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        headers = random_headers(asin, asin_to_kw)
        try:
            if USE_CLOUDSCRAPER:
                r = cs.get(f"https://www.amazon.in/dp/{asin}", headers=headers, timeout=30, proxies=proxies)
            else:
                r = session.get(f"https://www.amazon.in/dp/{asin}", headers=headers, timeout=30, proxies=proxies)
        except Exception as e:
            status = None
            html = f"REQUEST_EXCEPTION:{e}"
            # treat as transient and backoff
            sleep_time = BACKOFF_BASE ** attempt + random.random()
            print(f"    [attempt {attempt}] exception for {asin}: {e} — backing off {sleep_time:.1f}s")
            time.sleep(sleep_time)
            continue

        status = r.status_code
        html = r.text or ""
        html_lower = html.lower()
        bot_markers = sniff_bot(html_lower)
        pdp_found = has_pdp_signals(html_lower)

        # Save debug HTML (limited)
        debug_path = os.path.join(DEBUG_DIR, f"debug_{asin}.html")
        try:
            with open(debug_path, "w", encoding="utf-8") as fh:
                fh.write(html[:SAVE_HTML_LIMIT])
        except Exception as e:
            print(f"[WARN] Can't save debug HTML for {asin}: {e}")

        notes = []
        if status is None:
            notes.append("no_status")
        elif status >= 500:
            notes.append(f"status_{status}")
        if bot_markers:
            notes.append("bot_markers:" + ",".join(bot_markers))
        if status == 200 and pdp_found and not bot_markers:
            notes.append("looks_like_pdp")
        if status == 200 and not pdp_found:
            notes.append("no_pdp_markers")

        result = {
            "asin": asin,
            "status": status,
            "bot_markers": bot_markers,
            "pdp_found": pdp_found,
            "notes": ";".join(notes),
            "debug_path": debug_path,
            "html_snippet": html[:1500],
        }

        # Decide success/failure and whether to retry:
        # Retry on 5xx, or if bot_markers found (likely blocked)
        if (status is not None and status >= 500) or bot_markers:
            # exponential backoff with jitter
            if attempt < MAX_RETRIES:
                backoff = (BACKOFF_BASE ** attempt) + random.uniform(0.5, 2.5)
                print(f"    [attempt {attempt}] detected issue for {asin} (status={status}, bot={bool(bot_markers)}). Backing off {backoff:.1f}s then retrying.")
                time.sleep(backoff)
                continue
            else:
                print(f"    [attempt {attempt}] final attempt for {asin} failed with status={status}, bot_markers={bot_markers}")
        # Either success or final attempt reached — return result
        return result

    # If we exit loop without returning, return a failure placeholder
    return {
        "asin": asin,
        "status": "ERR",
        "bot_markers": ["max_retries_exceeded"],
        "pdp_found": False,
        "notes": "max_retries_exceeded",
        "debug_path": os.path.join(DEBUG_DIR, f"debug_{asin}.html"),
        "html_snippet": "",
    }

# ====== Main run ======
def main():
    if not os.path.exists(TRACK_YAML):
        print(f"[ERROR] YAML not found at {TRACK_YAML}")
        return

    cfg = load_yaml(TRACK_YAML)
    mapping = cfg.get("tracking", {}).get("keywords_asins", {})
    asin_to_kw = build_asins_map(mapping)
    all_asins = list(asin_to_kw.keys())
    if not all_asins:
        print("[INFO] No ASINs found in YAML.")
        return

    # randomize access order each run
    random.shuffle(all_asins)
    print(f"Checking {len(all_asins)} ASINs in random order...")

    session = requests.Session()
    results = []

    for i, asin in enumerate(all_asins, start=1):
        print(f"[{i}/{len(all_asins)}] => Fetching {asin} ...")
        res = fetch_with_backoff(asin, session, asin_to_kw, proxies=PROXIES)
        status = res.get("status")
        bot_detected = bool(res.get("bot_markers"))
        pdp = res.get("pdp_found")
        notes = res.get("notes", "")
        debug_path = res.get("debug_path")
        print(f"   status={status}  bot_detected={bot_detected}  pdp_found={pdp}  notes={notes}  debug={debug_path}")

        results.append({
            "asin": asin,
            "status": status if status is not None else "ERR",
            "bot_detected": "yes" if bot_detected else "no",
            "pdp_found": "yes" if pdp else "no",
            "notes": notes,
            "debug_path": debug_path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # short randomized sleep
        sleep_short = random.uniform(SHORT_SLEEP_LOW, SHORT_SLEEP_HIGH)
        time.sleep(sleep_short)

        # occasional long break to mimic human stepping away
        if random.random() < LONG_BREAK_PROB:
            long_break = random.uniform(LONG_BREAK_MIN, LONG_BREAK_MAX)
            print(f"   taking a longer break for {long_break:.0f}s to mimic human behavior...")
            time.sleep(long_break)

    # Save CSV summary
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["asin", "status", "bot_detected", "pdp_found", "notes", "debug_path", "timestamp"])
        for r in results:
            w.writerow([r["asin"], r["status"], r["bot_detected"], r["pdp_found"], r["notes"], r["debug_path"], r["timestamp"]])

    print(f"\nDone. Summary written to {OUT_CSV}")
    print(f"Debug HTML saved to {DEBUG_DIR} (open files for inspection)")

if __name__ == "__main__":
    main()
