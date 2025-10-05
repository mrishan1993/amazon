#!/usr/bin/env python3
# EC2-hardened Amazon rank + BSR tracker (requests + BeautifulSoup)
# Supports multiple ASINs with individual keywords
# Inputs: asins_keywords.yaml, email.yaml
# Output: results.csv and email

import os
import re
import csv
import ssl
import time
import yaml
import smtplib
import random
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from email.message import EmailMessage
from urllib.parse import quote_plus

# ---------------- Logging ----------------
def init_logging():
    logger = logging.getLogger("rank-bs4-ec2")
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
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------- Headers ----------------
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

# ---------------- YAML loaders ----------------
def load_yaml(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)

def load_keywords_yaml(filename="asins_keywords.yaml"):
    path = os.path.join(BASE_DIR, filename)
    logger.info(f"Loading ASINs file: {path}")
    cfg_list = load_yaml(path)

    out = []
    for entry in cfg_list:
        asin = entry.get("asin", "").strip()
        kws = entry.get("keywords", [])
        if not asin or not isinstance(kws, list):
            continue
        out.append({
            "asin": asin,
            "domain": "https://www.amazon.in",
            "keywords": [k.strip() for k in kws if k.strip()],
            "pincode": None
        })
    return out

def load_email_yaml(filename="email_akarsh.yaml"):
    path = os.path.join(BASE_DIR, filename)
    logger.info(f"Loading email file: {path}")
    cfg = load_yaml(path) or {}
    return cfg.get("email")

# ---------------- Session + Requests ----------------
def make_session(headers=None, proxy=None):
    sess = requests.Session()
    sess.headers.update(headers or BASE_HEADERS)
    if proxy:
        sess.proxies.update({"http": proxy, "https": proxy})
    return sess

def is_captcha(html_text):
    lt = html_text.lower()
    return (
        "validatecaptcha" in lt
        or "enter the characters you see below" in lt
        or "api-services-support@amazon.com" in lt
        or ("captcha" in lt and "amazon" in lt)
    )

def get_html(sess, url, referer=None, max_retries=3, sleep_range=(1.0, 2.2), timeout=35):
    for i in range(max_retries):
        try:
            headers = {"Referer": referer} if referer else {}
            r = sess.get(url, headers=headers, timeout=timeout)
            if r.status_code == 200 and not is_captcha(r.text):
                return r.text
            logger.warning(f"Non-200 or CAPTCHA {r.status_code} for {url} (try {i+1}/{max_retries})")
        except Exception as e:
            logger.warning(f"Request error {url} (try {i+1}/{max_retries}): {e}")
        time.sleep(random.uniform(*sleep_range))
    return None

def post_json(sess, url, payload, referer=None, extra_headers=None, max_retries=2, sleep_range=(0.8, 1.6), timeout=30):
    for i in range(max_retries + 1):
        try:
            headers = {"Referer": referer, "x-requested-with": "XMLHttpRequest", "accept": "application/json"}
            if extra_headers:
                headers.update(extra_headers)
            r = sess.post(url, json=payload, headers=headers, timeout=timeout)
            if r.status_code in (200, 204):
                return r
            logger.warning(f"POST non-200 {r.status_code} to {url} (try {i+1}/{max_retries+1})")
        except Exception as e:
            logger.warning(f"POST error {url} (try {i+1}/{max_retries+1}): {e}")
        if i < max_retries:
            time.sleep(random.uniform(*sleep_range))
    return None

def bootstrap_session(sess, domain, pincode=None):
    home = f"{domain}/"
    html = get_html(sess, home, referer=None, max_retries=3)
    if not html:
        logger.warning("Home bootstrap failed; proceeding anyway")

    if pincode:
        change_url = f"{domain}/portal-migration/hz/glow/address-change?actionSource=glow"
        payload = {
            "locationType": "LOCATION_INPUT",
            "zipCode": str(pincode),
            "storeContext": "generic",
            "deviceType": "web",
            "pageType": "Search",
            "actionSource": "glow",
        }
        resp = post_json(sess, change_url, payload, referer=home)
        if resp and resp.status_code in (200, 204):
            logger.info(f"Set delivery PIN {pincode} for session")
        else:
            logger.warning(f"Failed to set delivery PIN {pincode}; continuing without it")

# ---------------- PDP parsing ----------------
def parse_bsr_from_soup(soup):
    def clean(t): return re.sub(r"\s+", " ", t).strip()
    for li in soup.select("#detailBullets_feature_div li, #detailBulletsWrapper_feature_div li"):
        txt = clean(li.get_text(" ", strip=True))
        if "Best Sellers Rank" in txt:
            return txt
    for tr in soup.select("#productDetails_detailBullets_sections1 tr, #productDetails_db_sections tr"):
        txt = clean(tr.get_text(" ", strip=True))
        if "Best Sellers Rank" in txt:
            return txt
    node = soup.find(string=lambda s: isinstance(s, str) and "Best Sellers Rank" in s)
    return clean(node.parent.get_text(" ", strip=True)) if node else None

def parse_pdp_metrics(html):
    soup = BeautifulSoup(html, "html.parser")
    price, rating, reviews = None, None, None
    for sel in ["span.a-price span.a-offscreen", "#priceblock_ourprice", "#priceblock_dealprice", "#priceblock_saleprice", "#price_inside_buybox"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            price = el.get_text(strip=True); break
    for sel in ["span[data-hook='rating-out-of-text']", "#acrPopover span.a-icon-alt"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            rating = el.get_text(strip=True); break
    for sel in ["#acrCustomerReviewText", "span[data-hook='total-review-count']"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            reviews = el.get_text(strip=True); break
    bsr_text = parse_bsr_from_soup(soup)
    return {"price": price, "rating": rating, "review_count": reviews, "bsr": bsr_text}

def get_bsr(sess, domain, asin):
    url = f"{domain}/dp/{asin}"
    html = get_html(sess, url, referer=f"{domain}/")
    if not html:
        return "BSR not available"
    return parse_pdp_metrics(html)["bsr"] or "BSR not available"

# ---------------- Search parsing ----------------
def parse_search_asins_html(soup):
    # Extract ASINs from all possible containers
    cards = soup.select("div[data-component-type='s-search-result'][data-asin]")
    asins = [c.get("data-asin", "").strip() for c in cards if c.get("data-asin")]
    if asins:
        return asins

    cards_alt = soup.select("div.s-main-slot div.s-search-result[data-asin], div.s-search-results div.s-search-result[data-asin]")
    asins = [c.get("data-asin", "").strip() for c in cards_alt if c.get("data-asin")]
    if asins:
        return asins

    # Fallback: parse JSON embedded in page
    html = str(soup)
    seen, out = set(), []
    for m in re.finditer(r'["\']asin["\']\s*:\s*["\']([A-Z0-9]{10})["\']', html):
        a = m.group(1)
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out

def parse_search_asins_regex(html):
    seen, out = set(), []
    for m in re.finditer(r'data-asin="([A-Z0-9]{10})"', html):
        a = m.group(1)
        if a not in seen:
            seen.add(a); out.append(a)
    return out

def get_keyword_rank(sess, domain, asin, keyword, max_pages=10):
    enc_kw = quote_plus(keyword)
    abs_index = 0
    for page in range(1, max_pages + 1):
        url = f"{domain}/s?k={enc_kw}&page={page}&ref=sr_pg_{page}"
        referer = f"{domain}/s?k={enc_kw}&ref=nb_sb_noss"
        html = get_html(sess, url, referer=referer)
        if not html:
            time.sleep(1)
            continue
        soup = BeautifulSoup(html, "html.parser")
        tiles = parse_search_asins_html(soup)
        if not tiles:
            tiles = parse_search_asins_regex(html)
        for idx, a in enumerate(tiles, start=1):
            abs_index += 1
            if a == asin:
                return page, abs_index
        time.sleep(random.uniform(1.0, 2.0))
    return None, None

# ---------------- CSV + Email ----------------
def save_to_csv(data, filename="results.csv"):
    with open(filename, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Keyword", "ASIN", "Page", "Position", "BSR", "Timestamp"])
        for row in data:
            w.writerow(row)

def send_email(email_cfg, subject, body, attachment="results.csv"):
    msg = EmailMessage()
    msg["From"] = email_cfg["from"]
    raw_to = email_cfg.get("to", [])
    to_list = raw_to if isinstance(raw_to, list) else [raw_to]
    to_list = [t.strip() for t in to_list if t.strip()]
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    msg.set_content(body)
    with open(attachment, "rb") as f:
        msg.add_attachment(f.read(), maintype="text", subtype="csv", filename=os.path.basename(attachment))
    context = ssl.create_default_context()
    with smtplib.SMTP(email_cfg["smtp_server"], email_cfg["smtp_port"]) as server:
        server.starttls(context=context)
        server.login(email_cfg["from"], email_cfg["password"])
        server.sendmail(email_cfg["from"], to_list, msg.as_string())

# ---------------- Main ----------------
if __name__ == "__main__":
    asin_entries = load_keywords_yaml("asins_keywords.yaml")
    email_cfg = load_email_yaml("email.yaml")

    sess = make_session(headers=BASE_HEADERS)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results = []

    for entry in asin_entries:
        asin = entry["asin"]
        domain = entry["domain"]
        keywords = entry["keywords"]
        pincode = entry.get("pincode") or "110001"

        logger.info(f"Bootstrapping session for ASIN {asin} with PIN {pincode}")
        bootstrap_session(sess, domain, pincode=pincode)

        for keyword in keywords:
            page, pos = get_keyword_rank(sess, domain, asin, keyword, max_pages=10)
            bsr = get_bsr(sess, domain, asin)
            page_val = page if page else "Not found"
            pos_val = pos if pos else "Not found"
            print(f"{keyword} → {asin} → Page {page_val}, Position {pos_val} | BSR: {bsr}")
            results.append([keyword, asin, page_val, pos_val, bsr, timestamp])
            time.sleep(random.uniform(0.8, 1.4))

    save_to_csv(results, filename="results_akarsh.csv")

    if email_cfg:
        send_email(
            email_cfg,
            subject="Amazon Keyword Rank Report (EC2)",
            body=f"Rank report generated at {timestamp}.",
            attachment="results.csv"
        )

    print("\n✅ Results saved to results.csv and emailed.")
