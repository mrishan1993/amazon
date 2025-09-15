#!/usr/bin/env python3
# Amazon ASIN tracker using requests + BeautifulSoup (EC2-friendly, no Selenium).
# - Reads tracking.keywords_asins from track.yaml.
# - PDP: fetch price, rating, review count, BSR once per unique ASIN.
# - Search: find each ASIN's rank per keyword across pages via data-asin parsing with HTML + regex fallbacks.
# - Strong desktop headers, Accept-Language en-IN, per-request Referer, CAPTCHA detection/backoff, and retries.
# - Prints to terminal, writes timestamped CSV, and emails via a separate email.yaml (email dict at top level).

import os
import re
import ssl
import csv
import time
import yaml
import smtplib
import random
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.parse import quote_plus

# ----------------------------
# Terminal-only logging
# ----------------------------
def init_logging():
    logger = logging.getLogger("asin-tracker-bs4")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    logger.info("Terminal logging initialized")
    logger.info(f"cwd={os.getcwd()}")
    return logger

logger = init_logging()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DEFAULT_DOMAIN = "https://www.amazon.in"

# Robust desktop headers + Accept-Language for IN locale [21][22]
BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "DNT": "1",
}

# ----------------------------
# Config loaders
# ----------------------------
def load_yaml(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)  # use safe_load for untrusted YAML content [2][1]

def load_track_config(path="track.yaml"):
    path_abs = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
    logger.info(f"Loading track config: {path_abs}")
    return load_yaml(path_abs)  # expects top-level "tracking" with "keywords_asins" [1][2]

def load_email_config(path="email.yaml"):
    path_abs = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
    logger.info(f"Loading email config: {path_abs}")
    cfg = load_yaml(path_abs)
    email_cfg = cfg.get("email") if isinstance(cfg, dict) else None
    if not email_cfg:
        logger.info("No 'email' section found in email.yaml; email step will be skipped")
    return email_cfg  # separate file for email settings [1][2]

# ----------------------------
# HTTP helpers (requests.Session)
# ----------------------------
def make_session(headers=None, proxy=None):
    sess = requests.Session()
    sess.headers.update(headers or BASE_HEADERS)
    if proxy:
        sess.proxies.update({"http": proxy, "https": proxy})
    return sess  # session shares cookies across requests for stability [21]

def is_captcha(html_text):
    lt = html_text.lower()
    return (
        "validatecaptcha" in lt
        or "enter the characters you see below" in lt
        or "api-services-support@amazon.com" in lt
        or ("captcha" in lt and "amazon" in lt)
    )  # simple Robot Check detection for Amazon [23]

def get_html(sess, url, referer=None, max_retries=3, sleep_range=(1.0, 2.2), timeout=35):
    for i in range(max_retries):
        try:
            headers = {"Referer": referer} if referer else {}
            r = sess.get(url, headers=headers, timeout=timeout)
            if r.status_code == 200 and not is_captcha(r.text):
                return r.text  # success [21]
            logger.warning(f"Non-200 or CAPTCHA {r.status_code} for {url} (try {i+1}/{max_retries})")
        except Exception as e:
            logger.warning(f"Request error {url} (try {i+1}/{max_retries}): {e}")
        time.sleep(random.uniform(*sleep_range))
    return None  # give up for this URL after retries [23]

# ----------------------------
# PDP parsing (BSR, price, rating, reviews)
# ----------------------------
def parse_bsr_from_soup(soup):
    def clean(t): return re.sub(r"\s+", " ", t).strip()
    # Bullets [24]
    for li in soup.select("#detailBullets_feature_div li, #detailBulletsWrapper_feature_div li"):
        txt = clean(li.get_text(" ", strip=True))
        if "Best Sellers Rank" in txt:
            return txt  # BSR line [24]
    # Legacy table [24]
    for tr in soup.select("#productDetails_detailBullets_sections1 tr, #productDetails_db_sections tr"):
        txt = clean(tr.get_text(" ", strip=True))
        if "Best Sellers Rank" in txt:
            return txt  # BSR table [24]
    # Fallback anywhere [24]
    node = soup.find(string=lambda s: isinstance(s, str) and "Best Sellers Rank" in s)
    return clean(node.parent.get_text(" ", strip=True)) if node else None  # fallback [24]

def parse_pdp_metrics(html):
    soup = BeautifulSoup(html, "html.parser")
    # Price variants [24]
    price = None
    for sel in ["span.a-price span.a-offscreen", "#priceblock_ourprice", "#priceblock_dealprice", "#priceblock_saleprice", "#price_inside_buybox"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            price = el.get_text(strip=True); break
    # Rating variants [24]
    rating = None
    for sel in ["span[data-hook='rating-out-of-text']", "#acrPopover span.a-icon-alt"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            rating = el.get_text(strip=True); break
    # Reviews variants [24]
    reviews = None
    for sel in ["#acrCustomerReviewText", "span[data-hook='total-review-count']"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            reviews = el.get_text(strip=True); break
    # BSR [24]
    bsr_text = parse_bsr_from_soup(soup)
    return {"price": price, "rating": rating, "review_count": reviews, "bsr": bsr_text}  # structured PDP metrics [24]

def fetch_pdp(sess, domain, asin):
    url = f"{domain}/dp/{asin}"
    html = get_html(sess, url, referer=f"{domain}/", max_retries=3)  # referer improves acceptance [21]
    if not html:
        logger.error(f"PDP fetch failed (empty/blocked): {url}")
        return {"price": None, "rating": None, "review_count": None, "bsr": None, "url": url}  # fallback [21]
    metrics = parse_pdp_metrics(html)
    metrics["url"] = url
    logger.info(f"PDP {asin}: price={metrics['price']} rating={metrics['rating']} reviews={metrics['review_count']}")
    return metrics  # one PDP fetch per ASIN [24]

# ----------------------------
# Search parsing (rank by keyword)
# ----------------------------
def parse_search_asins_html(soup):
    # Server-rendered result tiles [25]
    cards = soup.select("div[data-component-type='s-search-result'][data-asin]")
    asins = [c.get("data-asin", "").strip() for c in cards if c.get("data-asin")]
    if asins:
        return asins  # stable rank ordering [25]
    # Alternative containers [25]
    cards_alt = soup.select("div.s-main-slot div.s-search-result[data-asin], div.s-search-results div.s-search-result[data-asin]")
    return [c.get("data-asin", "").strip() for c in cards_alt if c.get("data-asin")]  # fallback [25]

def parse_search_asins_regex(html):
    # Attribute fallback [25]
    seen = set(); out = []
    for m in re.finditer(r'data-asin="([A-Z0-9]{10})"', html):
        a = m.group(1)
        if a not in seen:
            seen.add(a); out.append(a)
    if out:
        return out  # attribute fallback [25]
    # JSON fallback [26]
    seen = set(); out = []
    for m in re.finditer(r'["\']asin["\']\s*:\s*["\']([A-Z0-9]{10})["\']', html):
        a = m.group(1)
        if a not in seen:
            seen.add(a); out.append(a)
    return out  # JSON fallback when HTML shifts [26]

def find_rank_for_asin(sess, domain, keyword, asin, max_pages=5, sleep_range=(0.8, 1.4)):
    abs_index = 0
    enc_kw = quote_plus(keyword)
    for page in range(1, max_pages + 1):
        url = f"{domain}/s?k={enc_kw}&page={page}&ref=sr_pg_{page}"
        referer = f"{domain}/s?k={enc_kw}&ref=nb_sb_noss"
        html = get_html(sess, url, referer=referer, max_retries=3)  # retries + referer [21]
        if not html:
            logger.warning(f"Search fetch failed (empty/blocked) p{page} for '{keyword}'"); continue  # next page [26]
        soup = BeautifulSoup(html, "html.parser")
        tiles = parse_search_asins_html(soup) or parse_search_asins_regex(html)  # resilient parsing [25][26]
        logger.info(f"Search '{keyword}' p{page}: tiles={len(tiles)} first10={tiles[:10]}")
        for idx, a in enumerate(tiles, start=1):
            abs_index += 1
            if a == asin:
                logger.info(f"FOUND {asin} page={page} pos={idx} abs={abs_index}")
                return {"page": page, "position": idx, "absolute": abs_index}  # rank detail [26]
        time.sleep(random.uniform(*sleep_range))  # jitter [27]
    logger.info(f"NOT FOUND {asin} within {max_pages} pages for '{keyword}'")
    return None  # continue pipeline [26]

# ----------------------------
# Email
# ----------------------------
def send_email(email_cfg, csv_file, subject="Daily Amazon ASIN Tracking Report"):
    logger.info("Sending email with CSV report")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_cfg["from"]
    # Normalize recipients (strip whitespace) and ensure list [6]
    raw_to = email_cfg.get("to", [])
    if isinstance(raw_to, str):
        to_list = [raw_to.strip()]
    else:
        to_list = [t.strip() for t in raw_to if isinstance(t, str) and t.strip()]
    msg["To"] = ", ".join(to_list)  # display header [6]
    msg.set_content("Attached is the daily ASIN tracking report.")
    with open(csv_file, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="csv", filename=os.path.basename(csv_file))
    context = ssl.create_default_context()
    with smtplib.SMTP(email_cfg["smtp_server"], email_cfg["smtp_port"]) as server:
        server.starttls(context=context)
        server.login(email_cfg["from"], email_cfg["password"])
        server.sendmail(email_cfg["from"], to_list, msg.as_string())  # envelope to multiple recipients [6]
    logger.info("Email sent successfully")  # per smtplib docs [10]

# ----------------------------
# Main
# ----------------------------
def main():
    track = load_track_config()  # track.yaml (keywords_asins, domain, etc.) [1]
    tracking = track.get("tracking", {})
    asin_map = tracking.get("keywords_asins", {})
    proxies = tracking.get("proxies", [])
    ua_list = tracking.get("user_agents", [])
    domain = tracking.get("domain", DEFAULT_DOMAIN)
    max_pages = int(tracking.get("max_pages", 5))

    proxy = random.choice(proxies) if proxies else None
    headers = BASE_HEADERS.copy()
    if ua_list:
        headers["User-Agent"] = random.choice(ua_list)  # optional UA rotation [21]
    logger.info(f"Domain={domain} Proxy={proxy} UA={headers['User-Agent']}")

    sess = make_session(headers=headers, proxy=proxy)

    # PDP metrics once per ASIN
    unique_asins = {a for lst in asin_map.values() for a in lst}
    asin_metrics = {}
    for a in unique_asins:
        asin_metrics[a] = fetch_pdp(sess, domain, a)
        time.sleep(random.uniform(0.6, 1.1))  # jitter [27]

    # Search ranks per keyword–ASIN
    rows = []
    for keyword, asins in asin_map.items():
        logger.info(f"=== Keyword: {keyword} ===")
        for a in asins:
            rank = find_rank_for_asin(sess, domain, keyword, a, max_pages=max_pages)
            base = asin_metrics.get(a, {"price": None, "rating": None, "review_count": None, "bsr": None, "url": f"{domain}/dp/{a}"})
            rows.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "keyword": keyword,
                "asin": a,
                "price": base.get("price"),
                "rating": base.get("rating"),
                "review_count": base.get("review_count"),
                "bsr": base.get("bsr"),
                "rank_page": rank["page"] if rank else None,
                "rank_position": rank["position"] if rank else None,
                "rank_absolute": rank["absolute"] if rank else None,
                "product_url": base.get("url"),
            })
            time.sleep(random.uniform(0.6, 1.1))  # jitter [27]

    # Save CSV next to script
    out = os.path.join(BASE_DIR, f"daily_amazon_tracking_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv")
    try:
        import pandas as pd
        pd.DataFrame(rows).to_csv(out, index=False)
    except Exception:
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["timestamp","keyword","asin","price","rating","review_count","bsr","rank_page","rank_position","rank_absolute","product_url"])
            for r in rows:
                w.writerow([r.get(k) for k in ["timestamp","keyword","asin","price","rating","review_count","bsr","rank_page","rank_position","rank_absolute","product_url"]])
    logger.info(f"Wrote CSV: {out}")  # output path [26]

    # Email via separate email.yaml
    email_cfg = load_email_config("email.yaml")
    if email_cfg:
        send_email(email_cfg, out)

if __name__ == "__main__":
    main()
