#!/usr/bin/env python3
# EC2/cron-ready ASIN tracker with headless Chrome and terminal-only logging.

import os
import re
import yaml
import time
import random
import logging
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
# Terminal-only logging
# ----------------------------
def init_logging():
    logger = logging.getLogger("asin-tracker")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # avoid duplicates via root
    if logger.handlers:
        logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    ch = logging.StreamHandler()  # stderr
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    logger.info("Terminal logging initialized")
    logger.info(f"cwd={os.getcwd()}")
    return logger

logger = init_logging()

# ----------------------------
# Config
# ----------------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

def load_config(path="track.yaml"):
    path_abs = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
    logger.info(f"Loading config: {path_abs}")
    with open(path_abs, "r") as f:
        return yaml.safe_load(f)

# ----------------------------
# Browser
# ----------------------------
def get_driver(proxy=None, headless=True, user_agent=None, chrome_binary=None, accept_language="en-IN,en;q=0.9"):
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    
    # SSL/Certificate fixes
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--ignore-ssl-errors")
    options.add_argument("--ignore-certificate-errors-spki-list")
    options.add_argument("--ignore-urlfetcher-cert-requests")
    options.add_argument("--disable-web-security")
    options.add_argument("--allow-running-insecure-content")
    options.add_argument("--insecure")
    options.add_argument("--allow-insecure-localhost")
    
    # Performance and stability
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")
    options.add_argument("--disable-images")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument("user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")

    # light fingerprint hardening (not a bypass)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    if user_agent:
        options.add_argument(f"--user-agent={user_agent}")
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")

    # Chrome binary path
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
# Bot check detection
# ----------------------------
def hit_robot_check(driver):
    url = (driver.current_url or "").lower()
    title = (driver.title or "").lower()
    if "validatecaptcha" in url or "robot check" in title:
        return True
    try:
        if driver.find_elements(By.CSS_SELECTOR, "form[action*='validateCaptcha']"):
            return True
    except Exception:
        pass
    return False

# ----------------------------
# Optional: set delivery PIN code
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
        try:
            pop = W(driver, 10).until(EC.element_to_be_clickable((By.ID, "nav-global-location-popover-link")))
        except TimeoutException:
            pop = W(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#glow-ingress-line2, #nav-global-location-popover-link")))
        pop.click()
        time.sleep(0.6)

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
            time.sleep(0.4)
            for by, sel in [
                (By.ID, "GLUXZipUpdate"),
                (By.CSS_SELECTOR, "span#GLUXZipUpdate input, input#GLUXZipUpdate"),
                (By.XPATH, "//input[@type='submit' and (contains(@aria-labelledby,'GLUXZipUpdate') or contains(@value,'Apply'))]"),
            ]:
                try:
                    btn = W(driver, 5).until(EC.element_to_be_clickable((by, sel)))
                    btn.click()
                    time.sleep(1.2)
                    break
                except TimeoutException:
                    continue
            logger.info("Delivery PIN applied")
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
# PDP scrape
# ----------------------------
def scrape_asin(driver, asin, wait_secs=20):
    product_url = f"https://www.amazon.in/dp/{asin}"
    logger.info(f"PDP open: {asin} -> {product_url}")
    data = {"asin": asin, "url": product_url}

    try:
        driver.get(product_url)
        W(driver, wait_secs).until(EC.presence_of_element_located((By.CSS_SELECTOR, "body")))
        time.sleep(random.uniform(0.7, 1.4))
    except Exception as e:
        logger.error(f"PDP load failed {asin}: {e}")
        return data

    if hit_robot_check(driver):
        logger.error("Robot Check on PDP")
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

    logger.info(f"PDP {asin}: price={data['price']} rating={data['rating']} reviews={data['review_count']}")
    return data

# ----------------------------
# Search rank
# ----------------------------
def get_search_rank(driver, keyword, asin, max_pages=5, wait_secs=20, pause=(1.4, 2.6)):
    logger.info(f"Search '{keyword}' for {asin}")
    abs_index = 0

    for page in range(1, max_pages + 1):
        search_url = f"https://www.amazon.in/s?k={keyword.replace(' ', '+')}&page={page}"
        try:
            driver.get(search_url)
            W(driver, wait_secs).until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.s-main-slot")))
            time.sleep(random.uniform(*pause))
        except Exception as e:
            logger.warning(f"Search load issue p{page}: {e}")
            continue

        if hit_robot_check(driver):
            logger.error("Robot Check on search")
            return None

        cards = driver.find_elements(By.CSS_SELECTOR, "div.s-main-slot div.s-search-result[data-asin]")
        data_asins = [c.get_attribute("data-asin") for c in cards if c.get_attribute("data-asin")]
        logger.info(f"Page {page}: tiles={len(data_asins)} first10={data_asins[:10]}")

        page_pos = 0
        for data_asin in data_asins:
            page_pos += 1
            abs_index += 1
            if data_asin == asin:
                logger.info(f"FOUND {asin} page={page} pos={page_pos} abs={abs_index}")
                return {"page": page, "position": page_pos, "absolute": abs_index}

        # pagination stop
        try:
            next_btn = driver.find_element(By.CSS_SELECTOR, "a.s-pagination-next")
            if "disabled" in next_btn.get_attribute("class"):
                break
        except NoSuchElementException:
            break

    logger.info(f"NOT FOUND {asin} within {max_pages} pages for '{keyword}'")
    return None

# ----------------------------
# Email
# ----------------------------
def send_email(config, csv_file):
    try:
        email_cfg = config["tracking"]["email"]
    except Exception:
        logger.info("Email not configured; skipping send.")
        return

    logger.info("Sending email with CSV report")
    msg = EmailMessage()
    msg["Subject"] = "Daily Amazon ASIN Tracking Report"
    msg["From"] = email_cfg["from"]
    msg["To"] = email_cfg["to"]
    msg.set_content("Attached is the daily ASIN tracking report.")

    with open(csv_file, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="csv", filename=os.path.basename(csv_file))

    try:
        with smtplib.SMTP(email_cfg["smtp_server"], email_cfg["smtp_port"]) as server:
            server.starttls()
            server.login(email_cfg["from"], email_cfg["password"])
            server.send_message(msg)
        logger.info("Email sent successfully")
    except Exception as e:
        logger.error(f"Email send failed: {e}")

# ----------------------------
# Main
# ----------------------------
def main():
    cfg = load_config()
    tracking = cfg.get("tracking", {})
    asin_map = tracking.get("keywords_asins", {})
    proxies = tracking.get("proxies", [])
    ua_list = tracking.get("user_agents", [])
    pincode = tracking.get("pincode")  # optional

    proxy = random.choice(proxies) if proxies else None
    ua = random.choice(ua_list) if ua_list else None
    logger.info(f"Proxy={proxy} UA={ua} PIN={pincode}")

    chrome_bin = "/usr/bin/google-chrome" if os.path.exists("/usr/bin/google-chrome") else None
    driver = get_driver(proxy=proxy, headless=True, user_agent=ua, chrome_binary=chrome_bin)

    rows = []
    try:
        # Normalize geo (optional but helps consistency)
        set_delivery_pin(driver, pincode)

        # PDP metrics once per ASIN
        unique_asins = {a for lst in asin_map.values() for a in lst}
        asin_metrics = {}
        for a in unique_asins:
            asin_metrics[a] = scrape_asin(driver, a)
            time.sleep(random.uniform(0.5, 1.1))

        # Search ranks
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
                time.sleep(random.uniform(0.5, 1.1))
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        logger.info("Driver closed")

    out = os.path.join(BASE_DIR, f"daily_amazon_tracking_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.csv")
    pd.DataFrame(rows).to_csv(out, index=False)
    logger.info(f"Wrote CSV: {out}")

if __name__ == "__main__":
    main()
