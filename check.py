#!/usr/bin/env python3
"""
check_scrape_blocking.py

Run this on your EC2 instance (same directory as track_akarsh.yaml).
It fetches each ASIN once, saves a debug HTML file, and reports whether
the response contains common Amazon anti-bot / error markers.

Output files:
 - debug_<ASIN>.html         (saved HTML response, up to first 100k chars)
 - fetch_debug_summary.csv   (summary CSV: asin,status,bot_flag,notes)

Usage:
    python3 check_scrape_blocking.py
"""

import os
import yaml
import requests
import random
import time
import csv
import re
from bs4 import BeautifulSoup
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRACK_YAML = os.path.join(BASE_DIR, "track_akarsh.yaml")
OUT_CSV = os.path.join(BASE_DIR, "fetch_debug_summary.csv")
DEBUG_DIR = os.path.join(BASE_DIR, "debug_html")
os.makedirs(DEBUG_DIR, exist_ok=True)

# simple pool of desktop-like User-Agents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko)"
    " Version/16.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/117.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:116.0) Gecko/20100101 Firefox/116.0",
]

# phrases that strongly suggest bot detection/CAPTCHA or Amazon blocking
BOT_MARKERS = [
    "enter the characters you see below",      # bot captcha prompt
    "validatecaptcha",                         # bot-check token
    "api-services-support@amazon.com",         # bot-check page text
    "to discuss automated access to amazon data",  # policy / block message
    "sorry! something went wrong",             # 5xx-ish Amazon messages
    "unusual traffic from your network",       # google-like wording sometimes returned
    "robot check",                             # robot-check
    "type the characters you see in the picture", # captcha wording
    "amazon.com has detected unusual traffic", # alternate
    "we've detected unusual activity",         # alternate bot wording
    "help us confirm you are a human",         # captcha wording
    "service temporarily unavailable",         # network issue
]

# also check for presence of certain tags that indicate a valid PDP
PDP_MARKERS = [
    "#productTitle",       # product title ID
    "feature-bullets",     # bullets section
    "dp",                  # dp pages often have /dp/ and product structure
]

# Optional: Use cloudscraper if you decide to enable it (uncomment install & import)
# pip install cloudscraper
# import cloudscraper
# scraper = cloudscraper.create_scraper()

# Optional: configure a proxy (example env var usage)
# PROXY = os.getenv("SCRAPER_PROXY")  # e.g. "http://user:pass@host:port"
# proxies = {"http": PROXY, "https": PROXY} if PROXY else None

def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def build_asins_set(mapping):
    s = set()
    for kw, lst in mapping.items():
        if isinstance(lst, list):
            for a in lst:
                if isinstance(a, str) and a.strip():
                    s.add(a.strip())
    return s

def sniff_bot(html_lower):
    """Return list of matching bot markers found in html_lower."""
    found = []
    for m in BOT_MARKERS:
        if m in html_lower:
            found.append(m)
    # a few regex checks for "captcha" or "aws|status" messages
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

def fetch_one_asin(asin, session, use_cloudscraper=False, proxies=None):
    url = f"https://www.amazon.in/dp/{asin}"
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        # If you uncomment cloudscraper usage earlier, switch here:
        # if use_cloudscraper:
        #     r = scraper.get(url, headers=headers, timeout=30)
        # else:
        r = session.get(url, headers=headers, timeout=30, proxies=proxies)
    except Exception as e:
        return {"status": None, "error": str(e), "html": None}

    status = r.status_code
    html = r.text or ""
    snippet = html[:200_000]  # limit size saved to disk
    # save debug html unconditionally for later inspection
    debug_path = os.path.join(DEBUG_DIR, f"debug_{asin}.html")
    try:
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(snippet)
    except Exception as e:
        # best-effort write
        print(f"[WARN] could not write debug file for {asin}: {e}")

    html_lower = html.lower()
    bot_markers_found = sniff_bot(html_lower)
    pdp_found = has_pdp_signals(html_lower)

    notes = []
    if status >= 500:
        notes.append(f"status_{status}")
    if bot_markers_found:
        notes.append("bot_markers:" + ",".join(bot_markers_found))
    if not pdp_found and status == 200:
        notes.append("no_pdp_markers")
    if status == 200 and pdp_found and not bot_markers_found:
        notes.append("looks_like_pdp")

    return {
        "status": status,
        "error": None,
        "html": snippet,
        "bot_markers": bot_markers_found,
        "pdp_found": pdp_found,
        "notes": ";".join(notes),
        "debug_path": debug_path,
    }

def main():
    if not os.path.exists(TRACK_YAML):
        print(f"[ERROR] YAML not found at {TRACK_YAML}")
        return

    cfg = load_yaml(TRACK_YAML)
    mapping = cfg.get("tracking", {}).get("keywords_asins", {})
    all_asins = sorted(build_asins_set(mapping))
    print(f"Found {len(all_asins)} unique ASINs to check")

    session = requests.Session()
    # proxies = proxies if you set PROXY env var; else None
    proxies = None

    results = []
    # Protect against rapid-fire requests: small pause between requests
    for i, asin in enumerate(all_asins, start=1):
        print(f"[{i}/{len(all_asins)}] Fetching ASIN {asin} ...")
        res = fetch_one_asin(asin, session, use_cloudscraper=False, proxies=proxies)
        status = res.get("status")
        bot = bool(res.get("bot_markers"))
        pdp = res.get("pdp_found")
        notes = res.get("notes") or ""
        debug_path = res.get("debug_path")
        print(f"   status={status}  bot_detected={bot}  pdp_found={pdp}  notes={notes}  debug={debug_path}")

        results.append({
            "asin": asin,
            "status": status if status is not None else "ERR",
            "bot_detected": "yes" if bot else "no",
            "pdp_found": "yes" if pdp else "no",
            "notes": notes,
            "debug_path": debug_path,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })

        # polite sleep (tune as needed)
        time.sleep(2.0 + random.random() * 2.0)

    # save summary CSV
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["asin", "status", "bot_detected", "pdp_found", "notes", "debug_path", "timestamp"])
        for r in results:
            w.writerow([r["asin"], r["status"], r["bot_detected"], r["pdp_found"], r["notes"], r["debug_path"], r["timestamp"]])

    print(f"\nDone. Summary written to {OUT_CSV}")
    print(f"Debug HTML saved to {DEBUG_DIR} (open files for inspection)")

if __name__ == "__main__":
    main()
