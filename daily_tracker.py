#!/usr/bin/env python3
# EC2 ASIN tracker using system Chrome (/usr/bin/google-chrome), headless=new, eager page loads, terminal-only logging.

import os
import re
import yaml
import time
import random
import logging
import smtplib
import pandas as pd
from datetime import datetime, timezone
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

# Try WebDriver Manager; otherwise rely on Selenium Manager (Selenium 4.6+)
USE_WDM = False
try:
    from webdriver_manager.chrome import ChromeDriverManager
    USE_WDM = True
except Exception:
    USE_WDM = False

# ----------------------------
# Terminal-only logging
# ----------------------------
def init_logging():
    logger = logging.getLogger("asin-tracker")
    logger.setLevel(logging.INFO)
    logger.propagate = False
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
def get_driver(proxy=None, headless=True, user_agent=None, accept_language="en-IN,en;q=0.9"):
    options = Options()

    # Force system Chrome binary
    chrome_bin = "/usr/bin/google-chrome"
    if not os.path.exists(chrome_bin):
        raise RuntimeError(f"Chrome binary not found at {chrome_bin}. Install Chrome or update path.")

    options.binary_location = chrome_bin

    # Modern headless; eager page-load to reduce renderer timeouts
    if headless:
        options.add_argument("--headless=new")
    options.page_load_strategy = "eager"

    # Server-stability flags
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=en-IN")
    options.add_argument(f"--accept-lang={accept_language}")
    options.add_argument("--blink-settings=imagesEnabled=false")  # disable images for speed

    # Light fingerprint hardening (not a bypass)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    if user_agent:
        options.add_argument(f"--user-agent={user_agent}")
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")

    # Prefer Selenium Manager; fallback to WebDriver Manager if present
    if USE_WDM:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)

    # Timeouts
    driver.set_page_load_timeout(60)
    driver.set_script_timeout(60)
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
def set_delivery_pin(driver, pincode, wait=25):
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
            pop = W(driver, 12).until(EC.element_to_be_clickable((By.ID, "nav-global-location-popover-link")))
        except TimeoutException:
            pop = W(driver, 12).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#glow-ingress-line2, #nav-global-location-popover-link")))
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
                pin_box = W(driver, 10).until(EC.presence_of_element_located((by, sel)))
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
                    btn = W(driver, 6).until(EC.element_to_be_clickable((by, sel)))
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

# Basic recovery if search grid is empty
def recover_search_page(driver, wait_secs=20):
    try:
        # Small scroll to trigger lazy blocks
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
        time.sleep(0.6)
        driver.execute_script("window.scrollTo(0, 0);")
        W(driver, wait_secs).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.s-main-slot")))
        return True
    except Exception:
        try:
            driver.refresh()
            W(driver, wait_secs).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.s-main-slot")))
            return True
        except Exception:
            return False

# ----------------------------
# PDP scrape
# ----------------------------
def scrape_asin(driver, asin, wait_secs=30):
    product_url = f"https://www.amazon.in/dp/{asin}"
    logger.info(f"PDP open: {asin} -> {product_url}")
    data = {"asin": asin, "url": product_url}

    try:
        driver.get(product_url)
        W(driver, wait_secs).until(EC.presence_of_element_located((By.CSS_SELECTOR, "body")))
        time.sleep(random.uniform(0.7, 1.2))
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
        m = re.search
