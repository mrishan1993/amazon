#!/usr/bin/env python3
# Amazon ASIN tracker using requests + BeautifulSoup (EC2-friendly, no Selenium).
# - Loads tracking config from track.yaml (same structure shown).
# - For each unique ASIN: fetch BSR from PDP.
# - For each keyword: scan up to max_pages of search results and locate ASIN rank.
# - Headers include User-Agent and Accept-Language; simple CAPTCHA detection/backoff.
# - Writes a timestamped CSV and can email it via SMTP config in track.yaml.

import os
import re
import csv
import ssl
import time
import yaml
import json
import smtplib
import random
import logging
import requests
from datetime import datetime, timezone
from email.message import EmailMessage
from bs4 import BeautifulSoup
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

# Robust desktop-like headers; Accept-Language helps locale consistency. [5][7]
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
        return yaml.safe_load(f)

# ----------------------------
# HTTP helpers (requests.Session)
# ----------------------------
def make_session(headers=None, proxy=None, timeout=30):
    sess = requests.Session()
    sess.headers.update(headers or BASE_HEADERS)
    sess.timeout = timeout  # not a real requests.Session attr; pass timeout explicitly
    if proxy:
        sess.proxies.update({"http": proxy, "https": proxy})
    return sess

def is_captcha(html_text):
    lt = html_text.lower()
    # Common markers for Amazon bot check pages. [5][12]
    return ("captcha" in lt and "amazon" in lt) or "api-services-support@amazon.com" in lt

def get_html(sess, url, max_retries=2, sleep_range=(1.0, 2.0)):
    for i in range(max_retries + 1):
        try:
            r = sess.get(url, timeout=35)
            if r.status_code == 200 and not is_captcha(r.text):
                return r.text
            logger.warning(f"Non-200 or CAPTCHA for {url} (try {i+1}/{max_retries+1}), status={r.status_code}")
        except Exception as e:
            logger.warning(f"Request error {url} (try {i+1}/{max_retries+1}): {e}")
        if i < max_retries:
            time.sleep(random.uniform(*sleep_range))
    return None

# ----------------------------
# PDP parsing (BSR, rating, reviews, price)
# ----------------------------
def parse_bsr_from_soup(soup):
    # Try bullets block [detailBullets_feature_div]; else productDetails tables. [8]
    # Normalize whitespace
    def clean_text(t):
        return re.sub(r"\s+", " ", t).strip()

    # Newer layout: detailBullets
    bullets = soup.select("#detailBullets_feature_div li, #detailBulletsWrapper_feature_div li")
    for li in bullets:
        txt = clean_text(li.get_text(" ", strip=True))
        if "Best Sellers Rank" in txt:
            return txt

    # Older layout: product details table
    rows = soup.select("#productDetails_detailBullets_sections1 tr, #productDetails_db_sections tr")
    for tr in rows:
        txt = clean_text(tr.get_text(" ", strip=True))
        if "Best Sellers Rank" in txt:
            return txt

    # Fallback: any element containing the phrase
    any_bsr = soup.find(string=lambda s: isinstance(s, str) and "Best Sellers Rank" in s)
    if any_bsr:
        return clean_text(any_bsr.parent.get_text(" ", strip=True))
    return None

def parse_pdp_metrics(html):
    soup = BeautifulSoup(html, "html.parser")
    # Price variants seen across locales. [8]
    price = None
    price_sel = [
        "span.a-price span.a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#priceblock_saleprice",
        "#price_inside_buybox",
    ]
    for sel in price_sel:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            price = el.get_text(strip=True)
            break

    # Rating
    rating = None
    rating_sel = [
        "span[data-hook='rating-out-of-text']",
        "#acrPopover span.a-icon-alt",
    ]
    for sel in rating_sel:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            rating = el.get_text(strip=True)
            break

    # Reviews
    reviews = None
    rev_sel = [
        "#acrCustomerReviewText",
        "span[data-hook='total-review-count']",
    ]
    for sel in rev_sel:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            reviews = el.get_text(strip=True)
            break

    # BSR
    bsr_text = parse_bsr_from_soup(soup)

    return {
        "price": price,
        "rating": rating,
        "review_count": reviews,
        "bsr": bsr_text,
    }

def fetch_pdp(sess, domain, asin):
    url = f"{domain}/dp/{asin}"
    html = get_html(sess, url)
    if not html:
        logger.error(f"PDP fetch failed (empty/blocked): {url}")
        return {"price": None, "rating": None, "review_count": None, "bsr": None, "url": url}
    metrics = parse_pdp_metrics(html)
    metrics["url"] = url
    logger.info(f"PDP {asin}: price={metrics['price']} rating={metrics['rating']} reviews={metrics['review_count']}")
    return metrics

# ----------------------------
# Search parsing (rank by keyword)
# ----------------------------
def parse_search_asins(html):
    # Parse search result tiles by data-asin within s-search-result. [4][2]
    soup = BeautifulSoup(html, "html.parser")
    # Many valid result containers have data-component-type='s-search-result'
    cards = soup.select("div.s-search-results div.s-search-result[data-asin], div.s-main-slot div.s-search-result[data-asin]")
    data_asins = []
    for c in cards:
        a = c.get("data-asin", "").strip()
        if a:
            data_asins.append(a)
    return data_asins

def find_rank_for_asin(sess, domain, keyword, asin, max_pages=5, sleep_range=(0.8, 1.6)):
    abs_index = 0
    enc_kw = quote_plus(keyword)
    for page in range(1, max_pages + 1):
        url = f"{domain}/s?k={enc_kw}&page={page}"
        html = get_html(sess, url)
        if not html:
            logger.warning(f"Search fetch failed (empty/blocked) p{page} for '{keyword}'")
            continue

        tiles = parse_search_asins(html)
        logger.info(f"Search '{keyword}' p{page}: tiles={len(tiles)} first10={tiles[:10]}")
        if tiles:
            for idx, a in enumerate(tiles, start=1):
                abs_index += 1
                if a == asin:
                    logger.info(f"FOUND {asin} page={page} pos={idx} abs={abs_index}")
                    return {"page": page, "position": idx, "absolute": abs_index}

        time.sleep(random.uniform(*sleep_range))
    logger.info(f"NOT FOUND {asin} within {max_pages} pages for '{keyword}'")
    return None

# ----------------------------
# Email
# ----------------------------
def send_email(email_cfg, csv_file, subject="Daily Amazon ASIN Tracking Report"):
    logger.info("Sending email with CSV report")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_cfg["from"]

    to_emails = email_cfg["to"] if isinstance(email_cfg["to"], list) else [email_cfg["to"]]
    msg["To"] = ", ".join(to_emails)
    msg.set_content("Attached is the daily ASIN tracking report.")

    with open(csv_file, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="csv", filename=os.path.basename(csv_file))

    context = ssl.create_default_context()
    with smtplib.SMTP(email_cfg["smtp_server"], email_cfg["smtp_port"]) as server:
        server.starttls(context=context)
        server.login(email_cfg["from"], email_cfg["password"])
        server.sendmail(email_cfg["from"], to_emails, msg.as_string())
    logger.info("Email sent successfully")

# ----------------------------
# Main
# ----------------------------
def main():
    cfg = load_config()
    tracking = cfg.get("tracking", {})
    asin_map = tracking.get("keywords_asins", {})
    proxies = tracking.get("proxies", [])
    ua_list = tracking.get("user_agents", [])

    # Domain is fixed to .in for this tracker; change if needed.
    domain = tracking.get("domain", DEFAULT_DOMAIN)

    proxy = random.choice(proxies) if proxies else None
    ua = random.choice(ua_list) if ua_list else None

    headers = BASE_HEADERS.copy()
    if ua:
        headers["User-Agent"] = ua

    logger.info(f"Domain={domain} Proxy={proxy} UA={headers['User-Agent']}")

    sess = make_session(headers=headers, proxy=proxy)

    # PDP metrics once per ASIN
    unique_asins = {a for lst in asin_map.values() for a in lst}
    asin_metrics = {}
    for a in unique_asins:
        asin_metrics[a] = fetch_pdp(sess, domain, a)
        time.sleep(random.uniform(0.6, 1.2))

    # For each keyword-ASIN, find rank
    rows = []
    for keyword, asins in asin_map.items():
        logger.info(f"=== Keyword: {keyword} ===")
        for a in asins:
            rank = find_rank_for_asin(sess, domain, keyword, a, max_pages=5)
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
            time.sleep(random.uniform(0.6, 1.2))

    # Write CSV
    out = os.path.join(BASE_DIR, f"daily_amazon_tracking_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv")
    import pandas as pd
    pd.DataFrame(rows).to_csv(out, index=False)
    logger.info(f"Wrote CSV: {out}")

    # Optional email
    if "email" in tracking:
        send_email(tracking["email"], out)

if __name__ == "__main__":
    main()
