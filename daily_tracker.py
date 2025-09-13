#!/usr/bin/env python3
import os
import re
import yaml
import time
import random
import smtplib
import pandas as pd
from datetime import datetime
from email.message import EmailMessage

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait as W
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    StaleElementReferenceException,
)

from webdriver_manager.chrome import ChromeDriverManager


# ----------------------------
# Config loading
# ----------------------------
def load_config(path="track.yaml"):
    with open(path, "r") as f:
        return yaml.safe_load(f)


# ----------------------------
# Browser setup (headless-friendly)
# ----------------------------
def get_driver(proxy=None, headless=True, user_agent=None, chrome_binary=None):
    options = Options()

    # Modern headless is recommended for servers/CI
    if headless:
        options.add_argument("--headless=new")

    # Server-safe flags for Linux VMs
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    # Reduce automation fingerprints (not a guarantee)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    if user_agent:
        options.add_argument(f"--user-agent={user_agent}")

    if proxy:
        options.add_argument(f"--proxy-server={proxy}")

    # Explicitly set Chrome binary if nonstandard path
    # Common on Ubuntu: /usr/bin/google-chrome
    if chrome_binary and os.path.exists(chrome_binary):
        options.binary_location = chrome_binary
    else:
        # Fall back to common Linux path if present
        if os.path.exists("/usr/bin/google-chrome"):
            options.binary_location = "/usr/bin/google-chrome"

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    driver.set_page_load_timeout(40)
    return driver


# ----------------------------
# Helpers
# ----------------------------
def safe_text(el):
    try:
        return el.text.strip()
    except Exception:
        return None


def first_text(driver, selectors):
    for by, sel in selectors:
        try:
            el = driver.find_element(by, sel)
            t = safe_text(el)
            if t:
                return t
        except Exception:
            continue
    return None


# ----------------------------
# Scrape metrics for an ASIN
# ----------------------------
def scrape_asin(driver, asin, wait_secs=15):
    product_url = f"https://www.amazon.in/dp/{asin}"
    print(f"🔗 Opening product page for ASIN: {asin} -> {product_url}")
    data = {"asin": asin, "url": product_url}

    try:
        driver.get(product_url)
        # Wait for a stable container on PDP
        W(driver, wait_secs).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "body"))
        )
        time.sleep(random.uniform(1.0, 2.5))
    except Exception as e:
        print(f"❌ Failed to open ASIN page: {asin} — {e}")
        return data

    # Price (current DOM tends to use a-price > a-offscreen)
    data["price"] = first_text(driver, [
        (By.CSS_SELECTOR, "span.a-price span.a-offscreen"),
        (By.ID, "priceblock_ourprice"),
        (By.ID, "priceblock_dealprice"),
        (By.ID, "priceblock_saleprice"),
    ])

    # Rating
    data["rating"] = first_text(driver, [
        (By.CSS_SELECTOR, "span[data-hook='rating-out-of-text']"),
        (By.CSS_SELECTOR, "#acrPopover span.a-icon-alt"),
    ])

    # Review count
    data["review_count"] = first_text(driver, [
        (By.ID, "acrCustomerReviewText"),
        (By.CSS_SELECTOR, "span[data-hook='total-review-count']"),
    ])

    # Best Sellers Rank (BSR) via known containers, else regex fallback
    bsr_text = None
    containers = [
        (By.ID, "detailBulletsWrapper_feature_div"),
        (By.ID, "productDetails_detailBullets_sections1"),
        (By.ID, "productDetails_db_sections"),
    ]
    for by, sel in containers:
        try:
            box = driver.find_element(by, sel)
            label = box.find_elements(By.XPATH, ".//*[contains(text(),'Best Sellers Rank')]")
            if label:
                bsr_text = box.text
                break
        except Exception:
            continue

    if not bsr_text:
        # Fallback: parse page source with a regex heuristic
        page_src = driver.page_source
        m = re.search(
            r"Best\s*Sellers\s*Rank[^#]*#\s*([\d,]+)\s*in\s*([^<\(\n]+)",
            page_src,
            re.IGNORECASE,
        )
        if m:
            bsr_text = f"#{m.group(1)} in {m.group(2).strip()}"

    data["bsr"] = bsr_text
    return data


# ----------------------------
# Find search rank for ASIN by keyword
# ----------------------------
def get_search_rank(driver, keyword, asin, max_pages=5, wait_secs=15, pause=(2.0, 4.0)):
    print(f"🔍 Searching for keyword '{keyword}' to find ASIN {asin}")
    abs_index = 0

    for page in range(1, max_pages + 1):
        search_url = f"https://www.amazon.in/s?k={keyword.replace(' ', '+')}&page={page}"
        try:
            driver.get(search_url)
            W(driver, wait_secs).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.s-main-slot div.s-search-result"))
            )
            time.sleep(random.uniform(*pause))
        except Exception as e:
            print(f"⚠️ Search load issue on page {page}: {e}")
            continue

        results = driver.find_elements(By.CSS_SELECTOR, "div.s-main-slot div.s-search-result")
        page_positions = 0
        for card in results:
            try:
                data_asin = card.get_attribute("data-asin")
                if not data_asin:
                    continue
                page_positions += 1
                abs_index += 1
                if data_asin == asin:
                    print(f"✅ Found ASIN {asin} at page {page}, position {page_positions}, absolute {abs_index}")
                    return {"page": page, "position": page_positions, "absolute": abs_index}
            except StaleElementReferenceException:
                continue

        # Stop early if next is disabled/missing
        try:
            next_btn = driver.find_element(By.CSS_SELECTOR, "a.s-pagination-next")
            if "disabled" in next_btn.get_attribute("class"):
                break
        except NoSuchElementException:
            break

    print(f"❌ ASIN {asin} not found within {max_pages} page(s) for '{keyword}'")
    return None


# ----------------------------
# Email report
# ----------------------------
def send_email(config, csv_file):
    print("📧 Sending email with CSV report...")
    msg = EmailMessage()
    msg["Subject"] = "Daily Amazon ASIN Tracking Report"
    msg["From"] = config["tracking"]["email"]["from"]
    msg["To"] = config["tracking"]["email"]["to"]
    msg.set_content("Attached is the daily ASIN tracking report.")

    with open(csv_file, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="csv", filename=csv_file)

    try:
        with smtplib.SMTP(
            config["tracking"]["email"]["smtp_server"],
            config["tracking"]["email"]["smtp_port"]
        ) as server:
            server.starttls()
            server.login(
                config["tracking"]["email"]["from"],
                config["tracking"]["email"]["password"]
            )
            server.send_message(msg)
        print("✅ Email sent successfully")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")


# ----------------------------
# Main
# ----------------------------
def main():
    cfg = load_config()

    proxies = cfg.get("tracking", {}).get("proxies", [])
    ua_list = cfg.get("tracking", {}).get("user_agents", [])
    asin_map = cfg.get("tracking", {}).get("keywords_asins", {})

    proxy = random.choice(proxies) if proxies else None
    ua = random.choice(ua_list) if ua_list else None
    print("Using proxy:", proxy)
    print("Using user-agent:", ua)

    # On Ubuntu servers, google-chrome is usually installed here
    chrome_bin = "/usr/bin/google-chrome" if os.path.exists("/usr/bin/google-chrome") else None

    driver = get_driver(proxy=proxy, headless=True, user_agent=ua, chrome_binary=chrome_bin)

    rows = []
    try:
        # Scrape product detail metrics once per unique ASIN
        unique_asins = {asin for asin_list in asin_map.values() for asin in asin_list}
        asin_metrics = {}
        for asin in unique_asins:
            asin_metrics[asin] = scrape_asin(driver, asin)
            time.sleep(random.uniform(0.8, 1.8))

        # Compute search ranks per keyword-ASIN pair
        for keyword, asins in asin_map.items():
            print(f"\n===== Keyword: {keyword} =====")
            for asin in asins:
                rank_info = get_search_rank(driver, keyword, asin, max_pages=5)
                base = asin_metrics.get(asin, {"asin": asin, "url": f"https://www.amazon.in/dp/{asin}"})
                row = {
                    "timestamp": datetime.utcnow().isoformat(),
                    "keyword": keyword,
                    "asin": asin,
                    "price": base.get("price"),
                    "rating": base.get("rating"),
                    "review_count": base.get("review_count"),
                    "bsr": base.get("bsr"),
                    "rank_page": rank_info["page"] if rank_info else None,
                    "rank_position": rank_info["position"] if rank_info else None,
                    "rank_absolute": rank_info["absolute"] if rank_info else None,
                    "product_url": base.get("url"),
                }
                rows.append(row)
                time.sleep(random.uniform(0.8, 1.8))
    finally:
        driver.quit()
        print("🔒 Driver closed.")

    # Save CSV
    df = pd.DataFrame(rows)
    out = f"daily_amazon_tracking_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.csv"
    df.to_csv(out, index=False)
    print(f"💾 CSV saved as {out}")

    # Email
    if "email" in cfg.get("tracking", {}):
        send_email(cfg, out)
    else:
        print("ℹ️ Email not configured; skipping send.")


if __name__ == "__main__":
    main()
