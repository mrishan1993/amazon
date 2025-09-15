#!/usr/bin/env python3
# Amazon ASIN tracker using requests + BeautifulSoup (EC2-friendly, no Selenium).
# - Reads "tracking.keywords_asins" from track.yaml (each keyword → list of ASINs).
# - Fetches PDP metrics (price, rating, reviews, BSR) once per unique ASIN.
# - Finds search rank per keyword–ASIN by parsing data-asin tiles across pages.
# - Robust headers, Accept-Language, simple CAPTCHA detection/backoff.
# - Prints to terminal, writes timestamped CSV, and emails via tracking.email (optional).

import os
import re
import ssl
import csv
import time
import yaml
import json
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

# Desktop-like headers + Accept-Language for locale consistency [12][18]
BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Connection": "keep-alive",
}

# ----------------------------
# Config
# ----------------------------
def load_config(path="track.yaml"):
    path_abs = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
    logger.info(f"Loading config: {path_abs}")
    with open(path_abs, "r") as f:
        return yaml.safe_load(f)  # expects a top-level "tracking" key with "keywords_asins" [21]

# ----------------------------
# HTTP helpers
# ----------------------------
def make_session(headers=None, proxy=None):
    sess = requests.Session()
    sess.headers.update(headers or BASE_HEADERS)
    if proxy:
        sess.proxies.update({"http": proxy, "https": proxy})
    return sess  # headers and proxies help reduce blocks at scale [15]

def is_captcha(html_text):
    lt = html_text.lower()
    return ("captcha" in lt and "amazon" in lt) or "api-services-support@amazon.com" in lt  # simple Robot Check detection [19]

def get_html(sess, url, max_retries=2, sleep_range=(1.0, 2.0), timeout=35):
    for i in range(max_retries + 1):
        try:
            r = sess.get(url, timeout=timeout)
            if r.status_code == 200 and not is_captcha(r.text):
                return r.text  # success path [12]
            logger.warning(f"Non-200 or CAPTCHA {r.status_code} for {url} (try {i+1}/{max_retries+1})")
        except Exception as e:
            logger.warning(f"Request error {url} (try {i+1}/{max_retries+1}): {e}")
        if i < max_retries:
            time.sleep(random.uniform(*sleep_range))
    return None  # give up after retries [19]

# ----------------------------
# PDP parsing (BSR, price, rating, reviews)
# ----------------------------
def parse_bsr_from_soup(soup):
    def clean(t):
        return re.sub(r"\s+", " ", t).strip()

    # Newer bullets area [detailBullets] [5]
    bullets = soup.select("#detailBullets_feature_div li, #detailBulletsWrapper_feature_div li")
    for li in bullets:
        txt = clean(li.get_text(" ", strip=True))
        if "Best Sellers Rank" in txt:
            return txt  # BSR text as-is [5]

    # Older table area [productDetails] [5]
    rows = soup.select("#productDetails_detailBullets_sections1 tr, #productDetails_db_sections tr")
    for tr in rows:
        txt = clean(tr.get_text(" ", strip=True))
        if "Best Sellers Rank" in txt:
            return txt  # BSR from legacy table [5]

    # Fallback search anywhere
    any_bsr = soup.find(string=lambda s: isinstance(s, str) and "Best Sellers Rank" in s)
    if any_bsr:
        return clean(any_bsr.parent.get_text(" ", strip=True))  # fallback parse [5]
    return None

def parse_pdp_metrics(html):
    soup = BeautifulSoup(html, "html.parser")
    # Price candidates [current selector often a-price > a-offscreen] [5]
    price = None
    for sel in [
        "span.a-price span.a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#priceblock_saleprice",
        "#price_inside_buybox",
    ]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            price = el.get_text(strip=True)
            break

    # Rating candidates [5]
    rating = None
    for sel in [
        "span[data-hook='rating-out-of-text']",
        "#acrPopover span.a-icon-alt",
    ]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            rating = el.get_text(strip=True)
            break

    # Review count candidates [5]
    reviews = None
    for sel in [
        "#acrCustomerReviewText",
        "span[data-hook='total-review-count']",
    ]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            reviews = el.get_text(strip=True)
            break

    # BSR via bullets/tables [5]
    bsr_text = parse_bsr_from_soup(soup)
    return {"price": price, "rating": rating, "review_count": reviews, "bsr": bsr_text}  # structured PDP metrics [5]

def fetch_pdp(sess, domain, asin):
    url = f"{domain}/dp/{asin}"
    html = get_html(sess, url)
    if not html:
        logger.error(f"PDP fetch failed: {url}")
        return {"price": None, "rating": None, "review_count": None, "bsr": None, "url": url}  # fallback on error [12]
    metrics = parse_pdp_metrics(html)
    metrics["url"] = url
    logger.info(f"PDP {asin}: price={metrics['price']} rating={metrics['rating']} reviews={metrics['review_count']}")
    return metrics  # one PDP fetch per unique ASIN [5]

# ----------------------------
# Search parsing (rank by keyword)
# ----------------------------
def parse_search_asins(html):
    # Parse result tiles by data-asin on s-search-result cards (stable rank anchor) [1]
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.s-main-slot div.s-search-result[data-asin], div.s-search-results div.s-search-result[data-asin]")
    data_asins = []
    for c in cards:
        a = (c.get("data-asin") or "").strip()
        if a:
            data_asins.append(a)
    return data_asins  # ordered list of ASINs for the page [1]

def find_rank_for_asin(sess, domain, keyword, asin, max_pages=5, sleep_range=(0.8, 1.6)):
    abs_index = 0
    enc_kw = quote_plus(keyword)
    for page in range(1, max_pages + 1):
        url = f"{domain}/s?k={enc_kw}&page={page}"
        html = get_html(sess, url)
        if not html:
            logger.warning(f"Search fetch failed p{page} for '{keyword}'")
            continue  # retry next page [21]
        tiles = parse_search_asins(html)
        logger.info(f"Search '{keyword}' p{page}: tiles={len(tiles)} first10={tiles[:10]}")
        for idx, a in enumerate(tiles, start=1):
            abs_index += 1
            if a == asin:
                logger.info(f"FOUND {asin} page={page} pos={idx} abs={abs_index}")
                return {"page": page, "position": idx, "absolute": abs_index}  # rank info [21]
        time.sleep(random.uniform(*sleep_range))
    logger.info(f"NOT FOUND {asin} within {max_pages} pages for '{keyword}'")
    return None  # not found within bounds [21]

# ----------------------------
# Email
# ----------------------------
def send_email(email_cfg, csv_file, subject="Daily Amazon ASIN Tracking Report"):
    logger.info("Sending email with CSV report")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_cfg["from"]
    to_list = email_cfg["to"] if isinstance(email_cfg["to"], list) else [email_cfg["to"]]
    msg["To"] = ", ".join(to_list)
    msg.set_content("Attached is the daily ASIN tracking report.")
    with open(csv_file, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="csv", filename=os.path.basename(csv_file))
    context = ssl.create_default_context()
    with smtplib.SMTP(email_cfg["smtp_server"], email_cfg["smtp_port"]) as server:
        server.starttls(context=context)
        server.login(email_cfg["from"], email_cfg["password"])
        server.sendmail(email_cfg["from"], to_list, msg.as_string())
    logger.info("Email sent successfully")  # standard SMTP flow with TLS [20]

# ----------------------------
# Main
# ----------------------------
def main():
    cfg = load_config()
    tracking = cfg.get("tracking", {})
    asin_map = tracking.get("keywords_asins", {})
    proxies = tracking.get("proxies", [])
    ua_list = tracking.get("user_agents", [])
    domain = tracking.get("domain", DEFAULT_DOMAIN)
    max_pages = int(tracking.get("max_pages", 5))

    proxy = random.choice(proxies) if proxies else None
    headers = BASE_HEADERS.copy()
    if ua_list:
        headers["User-Agent"] = random.choice(ua_list)  # optional UA rotation [12]

    logger.info(f"Domain={domain} Proxy={proxy} UA={headers['User-Agent']}")

    sess = make_session(headers=headers, proxy=proxy)

    # PDP metrics once per ASIN
    unique_asins = {a for lst in asin_map.values() for a in lst}
    asin_metrics = {}
    for a in unique_asins:
        asin_metrics[a] = fetch_pdp(sess, domain, a)
        time.sleep(random.uniform(0.6, 1.2))  # jitter to reduce blocks [15]

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
            time.sleep(random.uniform(0.6, 1.2))  # jitter [15]

    # Save CSV
    out = os.path.join(BASE_DIR, f"daily_amazon_tracking_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv")
    try:
        import pandas as pd
        pd.DataFrame(rows).to_csv(out, index=False)
    except Exception:
        # Minimal CSV writer fallback
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["timestamp","keyword","asin","price","rating","review_count","bsr","rank_page","rank_position","rank_absolute","product_url"])
            for r in rows:
                w.writerow([r.get(k) for k in ["timestamp","keyword","asin","price","rating","review_count","bsr","rank_page","rank_position","rank_absolute","product_url"]])
    logger.info(f"Wrote CSV: {out}")  # file next to this script [21]

    # Optional email via tracking.email
    if "email" in tracking:
        send_email(tracking["email"], out)

if __name__ == "__main__":
    main()
