#!/usr/bin/env python3
import os
import re
import yaml
import time
import random
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
import pandas as pd

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
# Paths
# ----------------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
SHOT_DIR = os.path.join(BASE_DIR, "shots")
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(SHOT_DIR, exist_ok=True)

# ----------------------------
# Logging
# ----------------------------
logger = logging.getLogger("asin-tracker")
logger.setLevel(logging.INFO)
handler = RotatingFileHandler(
    os.path.join(LOG_DIR, "tracker.log"),
    maxBytes=5_000_000,
    backupCount=5,
    encoding="utf-8"
)
fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
handler.setFormatter(fmt)
logger.addHandler(handler)

def snap(driver, name):
    path = os.path.join(SHOT_DIR, name)
    try:
        ok = driver.save_screenshot(path)
        logger.info(f"Saved screenshot: {path} (ok={ok})")
    except Exception as e:
        logger.warning(f"Failed to save screenshot {path}: {e}")

# ----------------------------
# Config loading
# ----------------------------
def load_config(path="track.yaml"):
    with open(path, "r") as f:
        return yaml.safe_load(f)

# ----------------------------
# Browser setup (headless-friendly)
# ----------------------------
def get_driver(proxy=None, headless=True, user_agent=None, chrome_binary=None, accept_language="en-IN,en;q=0.9"):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"--lang=en-IN")
    options.add_argument(f"--accept-lang={accept_language}")
    options.add_argument(f"--force-device-scale-factor=1")

    # light fingerprint hardening (not a bypass)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    if user_agent:
        options.add_argument(f"--user-agent={user_agent}")
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")

    if chrome_binary and os.path.exists(chrome_binary):
        options.binary_location = chrome_binary
    elif os.path.exists("/usr/bin/google-chrome"):
        options.binary_location = "/usr/bin/google-chrome"

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    driver.set_page_load_timeout(45)
    return driver

# ----------------------------
# Bot-check / CAPTCHA detection
# ----------------------------
def hit_robot_check(driver):
    url = (driver.current_url or "").lower()
    title = (driver.title or "").lower()
    if "validatecaptcha" in url or "robot check" in title:
        return True
    # crude heuristic: no result grid + captcha markers
    try:
        if driver.find_elements(By.CSS_SELECTOR, "form[action*='validateCaptcha']"):
            return True
    except Exception:
        pass
    return False

# ----------------------------
# Optional: set delivery PIN code (geo affects results)
# ----------------------------
def set_delivery_pin(driver, pincode, wait=15):
    if not pincode:
        return
    logger.info(f"Setting delivery PIN: {pincode}")
    driver.get("https://www.amazon.in/")
    try:
        W(driver, wait).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(random.uniform(0.8, 1.5))
    except Exception as e:
        logger.warning(f"Home not ready: {e}")
        return
    try:
        # Open location popover
        try:
            pop = W(driver, 10).until(EC.element_to_be_clickable((By.ID, "nav-global-location-popover-link")))
        except TimeoutException:
            pop = W(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#glow-ingress-line2, #nav-global-location-popover-link")))
        pop.click()
        time.sleep(0.8)

        # Input pin (ids vary)
        pin_inputs = [
            (By.ID, "GLUXZipUpdateInput"),
            (By.ID, "GLUXZipUpdateInput_0"),
            (By.CSS_SELECTOR, "input[name='GLUXZipUpdateInput']"),
        ]
        pin_box = None
        for by, sel in pin_inputs:
            try:
                pin_box = W(driver, 8).until(EC.presence_of_element_located((by, sel)))
                break
            except TimeoutException:
                continue
        if pin_box:
            pin_box.clear()
            pin_box.send_keys(str(pincode))
            time.sleep(0.5)
            # Apply/Continue
            for by, sel in [
                (By.ID, "GLUXZipUpdate"),
                (By.CSS_SELECTOR, "span#GLUXZipUpdate input, input#GLUXZipUpdate"),
                (By.XPATH, "//input[@type='submit' and (contains(@aria-labelledby,'GLUXZipUpdate') or contains(@value,'Apply'))]"),
            ]:
                try:
                    btn = W(driver, 5).until(EC.element_to_be_clickable((by, sel)))
                    btn.click()
                    time.sleep(1.5)
                    break
                except TimeoutException:
                    continue
            logger.info("Delivery PIN applied")
            snap(driver, f"pin_{pincode}.png")
        else:
            logger.info("PIN input not found; skipping")
    except Exception as e:
        logger.warning(f"PIN set failed: {e}")

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
def scrape_asin(driver, asin, wait_secs=20):
    product_url = f"https://www.amazon.in/dp/{asin}"
    logger.info(f"Open PDP: {asin} -> {product_url}")
    data = {"asin": asin, "url": product_url}

    try:
        driver.get(product_url)
        W(driver, wait_secs).until(EC.presence_of_element_located((By.CSS_SELECTOR, "body")))
        time.sleep(random.uniform(0.8, 1.5))
        snap(driver, f"pdp_{asin}.png")
    except Exception as e:
        logger.error(f"PDP load failed {asin}: {e}")
        return data

    if hit_robot_check(driver):
        logger.error("Robot Check encountered on PDP")
        snap(driver, f"robot_pdp_{asin}.png")
        return data

    data["price"] = first_text(driver, [
        (By.CSS_SELECTOR, "span.a-price span.a-offscreen"),
        (By.ID, "priceblock_ourprice"),
        (By.ID, "priceblock_dealprice"),
        (By.ID, "priceblock_saleprice"),
    ])

    data["rating"] = first_text(driver, [
        (By.CSS_SELECTOR, "span[data-hook='rating-out-of-text']"),
        (By.CSS_SELECTOR, "#acrPopover span.a-icon-alt"),
    ])

    data["review_count"] = first_text(driver, [
        (By.ID, "acrCustomerReviewText"),
        (By.CSS_SELECTOR, "span[data-hook='total-review-count']"),
    ])

    bsr_text = None
    for by, sel in [
        (By.ID, "detailBulletsWrapper_feature_div"),
        (By.ID, "productDetails_detailBullets_sections1"),
        (By.ID, "productDetails_db_sections"),
    ]:
        try:
            box = driver.find_element(by, sel)
            if box.find_elements(By.XPATH, ".//*[contains(text(),'Best Sellers Rank')]"):
                bsr_text = box.text
                break
        except Exception:
            continue
    if not bsr_text:
        page_src = driver.page_source
        m = re.search(r"Best\s*Sellers\s*Rank[^#]*#\s*([\d,]+)\s*in\s*([^<\(\n]+)", page_src, re.IGNORECASE)
        if m:
            bsr_text = f"#{m.group(1)} in {m.group(2).strip()}"
    data["bsr"] = bsr_text

    logger.info(f"PDP metrics {asin}: price={data['price']} rating={data['rating']} reviews={data['review_count']}")
    return data

# ----------------------------
# Find search rank for ASIN by keyword
# ----------------------------
def get_search_rank(driver, keyword, asin, max_pages=5, wait_secs=20, pause=(1.6, 3.2)):
    logger.info(f"Search '{keyword}' for ASIN {asin}")
    abs_index = 0

    for page in range(1, max_pages + 1):
        search_url = f"https://www.amazon.in/s?k={keyword.replace(' ', '+')}&page={page}"
        try:
            driver.get(search_url)
            W(driver, wait_secs).until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.s-main-slot")))
            time.sleep(random.uniform(*pause))
            snap(driver, f"search_{keyword.replace(' ','_')}_p{page}.png")
        except Exception as e:
            logger.warning(f"Search load issue page {page}: {e}")
            continue

        if hit_robot_check(driver):
            logger.error("Robot Check encountered on search")
            snap(driver, f"robot_search_{keyword.replace(' ','_')}_p{page}.png")
            return None

        cards = driver.find_elements(By.CSS_SELECTOR, "div.s-main-slot div.s-search-result[data-asin]")
        data_asins = [c.get_attribute("data-asin") for c in cards if c.get_attribute("data-asin")]
        logger.info(f"Page {page}: {len(data_asins)} result tiles; first 10 ASINs: {data_asins[:10]}")

        page_pos = 0
        for data_asin in data_asins:
            page_pos += 1
            abs_index += 1
            if data_asin == asin:
                logger.info(f"FOUND {asin} on page {page} pos {page_pos} abs {abs_index}")
                return {"page": page, "position": page_pos, "absolute": abs_index}

        # Pagination guard
        try:
            next_btn = driver.find_element(By.CSS_SELECTOR, "a.s-pagination-next")
            if "disabled" in next_btn.get_attribute("class"):
                break
        except NoSuchElementException:
            break

    logger.info(f"NOT FOUND {asin} within {max_pages} page(s) for '{keyword}'")
    return None

# ----------------------------
# Main
# ----------------------------
def main():
    cfg = load_config()
    tracking = cfg.get("tracking", {})
    asin_map = tracking.get("keywords_asins", {})
    proxies = tracking.get("proxies", [])
    ua_list = tracking.get("user_agents", [])
    pincode = tracking.get("pincode")  # optional: set a consistent PIN

    proxy = random.choice(proxies) if proxies else None
    ua = random.choice(ua_list) if ua_list else None
    logger.info(f"Proxy={proxy} UA={ua} PIN={pincode}")

    chrome_bin = "/usr/bin/google-chrome" if os.path.exists("/usr/bin/google-chrome") else None
    driver = get_driver(proxy=proxy, headless=True, user_agent=ua, chrome_binary=chrome_bin)

    rows = []
    try:
        # Normalize location (optional but recommended)
        set_delivery_pin(driver, pincode)

        # Scrape PDP metrics once per ASIN
        unique_asins = {a for lst in asin_map.values() for a in lst}
        asin_metrics = {}
        for a in unique_asins:
            asin_metrics[a] = scrape_asin(driver, a)
            time.sleep(random.uniform(0.6, 1.2))

        # Search ranks per keyword-ASIN
        for keyword, asins in asin_map.items():
            logger.info(f"=== Keyword: {keyword} ===")
            for a in asins:
                rank = get_search_rank(driver, keyword, a, max_pages=5)
                base = asin_metrics.get(a, {"asin": a, "url": f"https://www.amazon.in/dp/{a}"})
                rows.append({
                    "timestamp": datetime.utcnow().isoformat(),
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
    finally:
        driver.quit()
        logger.info("Driver closed")

    out = f"daily_amazon_tracking_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.csv"
    pd.DataFrame(rows).to_csv(out, index=False)
    logger.info(f"Wrote CSV: {out}")

if __name__ == "__main__":
    main()
