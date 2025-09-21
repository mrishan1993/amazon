#!/usr/bin/env python3
"""
Robust Amazon targeted clicker

- Infinite loop over keywords -> ASIN lists
- Retries clicks, falls back to direct product URL if needed
- Restarts chromedriver/webdriver on crashes
- Randomized delays and small scrolling for "human" behavior
- Logs to stdout
- Config file "ctr.yaml" with:
  asin_map:
    "keyword one": ["ASIN1", "ASIN2"]
    "keyword two": ["ASIN3"]
  proxies: ["http://1.2.3.4:3128"] (optional)
"""

import yaml
import random
import time
import sys
import os
import traceback
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ---------- Configurable ----------
CFG_PATH = "ctr.yaml"
CHROME_DRIVER_PATH = None  # set to path if chromedriver not in PATH, else None
HEADLESS = True            # you can set to False for debugging
MAX_CLICK_RETRIES = 3
KEYWORD_LOOP_DELAY = (2, 5)  # pause between keywords (s)
BROWSE_TIME_RANGE = (5, 10)  # time to spend on search page before clicking an ASIN
STAY_TIME_RANGE = (1.5, 3.5) # time to stay on ASIN page
BACKOFF_BASE = 5             # seconds before restart, multiplied on repeated crashes
# ----------------------------------

def load_config(path=CFG_PATH):
    try:
        with open(path, "r") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[{now()}] ❌ Failed to load config {path}: {e}")
        sys.exit(1)

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def make_driver(headless=HEADLESS, proxy=None):
    opts = Options()
    if headless:
        # some sites detect headless. You can run non-headless if detection is a problem.
        opts.add_argument("--headless=new")
    # general hardening
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1200,1400")
    # optional proxy
    if proxy:
        opts.add_argument(f"--proxy-server={proxy}")

    # try to reduce detection
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option('useAutomationExtension', False)
    # set a common user-agent
    opts.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")

    # create service and driver
    service = Service(CHROME_DRIVER_PATH) if CHROME_DRIVER_PATH else Service()
    driver = webdriver.Chrome(service=service, options=opts)
    # basic stealth: set webdriver property off (best-effort)
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined})
"""
        })
    except Exception:
        pass
    return driver

def human_sleep(a, b=None):
    if b is None:
        b = a
    t = random.uniform(a, b)
    time.sleep(t)
    return t

def open_search(driver, keyword):
    url = f"https://www.amazon.in/s?k={keyword.replace(' ', '+')}"
    driver.get(url)
    # small scroll to simulate user
    human_sleep(0.5, 1.0)
    try:
        driver.execute_script("window.scrollBy(0, 200);")
    except Exception:
        pass

def click_asin(driver, asin):
    """
    Attempts several methods to click the ASIN block:
      1) Find element with data-asin and click it (native)
      2) Find descendant anchor and click it
      3) Use JS to click the anchor
      4) Open product detail URL directly as fallback
    Returns True if navigation to product detail looks successful.
    """
    xpath_root = f"//div[@data-asin='{asin}' and normalize-space(@data-asin)!='']"
    try:
        # wait for the element to appear
        elem = WebDriverWait(driver, 6).until(
            EC.presence_of_element_located((By.XPATH, xpath_root))
        )
    except Exception:
        print(f"[{now()}] ❌ ASIN root not found in search results: {asin}")
        return False

    # try strategies
    for attempt in range(1, MAX_CLICK_RETRIES + 1):
        try:
            # strategy A: clickable anchor inside the ASIN block
            try:
                anchor = elem.find_element(By.XPATH, ".//a[@class and (contains(@href,'/dp/') or contains(@href,'/gp/'))]")
            except Exception:
                anchor = None

            if anchor:
                try:
                    # scroll into view and click by JS or action chain
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", anchor)
                    human_sleep(0.4, 0.9)
                    try:
                        anchor.click()
                    except Exception:
                        # fallback to action chain
                        ActionChains(driver).move_to_element(anchor).click(anchor).perform()
                    print(f"[{now()}] ✅ Clicked anchor for ASIN {asin} (attempt {attempt})")
                    return wait_for_product_page(driver, asin)
                except Exception as e:
                    print(f"[{now()}] ⚠ anchor click failed for ASIN {asin}: {e}")

            # strategy B: click on whole result tile (elem)
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
                human_sleep(0.3, 0.7)
                try:
                    elem.click()
                except Exception:
                    ActionChains(driver).move_to_element(elem).click(elem).perform()
                print(f"[{now()}] ✅ Clicked result tile for ASIN {asin} (attempt {attempt})")
                return wait_for_product_page(driver, asin)
            except Exception as e:
                print(f"[{now()}] ⚠ tile click failed for ASIN {asin}: {e}")

            # strategy C: try JS click on first anchor inside the tile
            try:
                js = ("(function(el){var a=el.querySelector('a'); if(a){a.click(); return true;} return false;})(arguments[0]);")
                ok = driver.execute_script(js, elem)
                if ok:
                    print(f"[{now()}] ✅ JS-clicked anchor inside tile for ASIN {asin} (attempt {attempt})")
                    return wait_for_product_page(driver, asin)
            except Exception as e:
                print(f"[{now()}] ⚠ js click failed for ASIN {asin}: {e}")

        except Exception as e:
            print(f"[{now()}] Exception during click attempts: {e}")
            # continue retry loop

        # small jitter before next attempt
        human_sleep(0.5, 1.2)

    # final fallback: open product page directly (less stealthy but reliable)
    prod_url = f"https://www.amazon.in/dp/{asin}"
    try:
        driver.get(prod_url)
        print(f"[{now()}] → Fallback: opened product URL for ASIN {asin}")
        return wait_for_product_page(driver, asin)
    except Exception as e:
        print(f"[{now()}] ❌ Fallback open failed for ASIN {asin}: {e}")
        return False

def wait_for_product_page(driver, asin, timeout=8):
    """
    Heuristics to decide whether we are on the product page:
    - URL contains '/dp/ASIN' or presence of product title id
    """
    try:
        WebDriverWait(driver, timeout).until(
            EC.any_of(
                EC.url_contains(f"/dp/{asin}"),
                EC.presence_of_element_located((By.ID, "productTitle")),
                EC.presence_of_element_located((By.ID, "title"))
            )
        )
        human_sleep(0.5, 1.2)
        return True
    except Exception:
        # sometimes site shows a popup; still treat as failure
        return False

def iterate_once(driver, asin_map):
    """
    One pass over all keywords in the asin_map
    """
    for keyword, asin_list in asin_map.items():
        try:
            open_search(driver, keyword)
            browse = human_sleep(*BROWSE_TIME_RANGE)
            print(f"[{now()}] ⌛ Browsed search for '{keyword}' ~{browse:.1f}s")
            # small scrolls to vary position
            try:
                driver.execute_script("window.scrollBy(0, 200 + Math.floor(Math.random()*300));")
            except Exception:
                pass
            human_sleep(0.5, 1.5)

            for asin in asin_list:
                clicked = click_asin(driver, asin)
                if clicked:
                    stay = human_sleep(*STAY_TIME_RANGE)
                    print(f"[{now()}] ↩️ Stayed on ASIN {asin} ~{stay:.1f}s")
                    # attempt to go back to search results; if fail, re-open search
                    try:
                        driver.back()
                        WebDriverWait(driver, 6).until(
                            EC.presence_of_element_located((By.ID, "twotabsearchtextbox"))
                        )
                        # scroll a bit to change viewport
                        driver.execute_script("window.scrollBy(0, 200);")
                        human_sleep(0.8, 1.8)
                        print(f"[{now()}] 🔄 Back to search page '{keyword}'")
                    except Exception:
                        print(f"[{now()}] ⚠ Could not navigate back cleanly, reloading search")
                        open_search(driver, keyword)
                        human_sleep(1, 2)
                else:
                    print(f"[{now()}] ⚠ SKIPPING ASIN {asin} (click failed)")

                # small jitter between ASINs
                human_sleep(0.8, 1.6)

            # delay a bit between keywords
            human_sleep(*KEYWORD_LOOP_DELAY)
        except Exception as e:
            print(f"[{now()}] Exception iterating keyword '{keyword}': {e}")
            traceback.print_exc()
            # continue to next keyword

def run_forever(config):
    asin_map = config.get("asin_map") or {}
    if not asin_map:
        print(f"[{now()}] ❌ No asin_map in config. Exiting.")
        return

    proxies = config.get("proxies", []) or []
    proxy = random.choice(proxies) if proxies else None
    print(f"[{now()}] 🌐 Using proxy: {proxy or 'None'}")

    crash_count = 0
    driver = None

    while True:
        try:
            if driver is None:
                print(f"[{now()}] ▶ Starting webdriver...")
                driver = make_driver(headless=HEADLESS, proxy=proxy)
                crash_count = 0

            # perform one full iteration over the map
            iterate_once(driver, asin_map)

            # loop rest, then repeat
            human_sleep(1, 2)

        except KeyboardInterrupt:
            print(f"[{now()}] ⛔ KeyboardInterrupt received — quitting.")
            try:
                if driver:
                    driver.quit()
            except:
                pass
            break

        except Exception as e:
            # catch chromedriver/browser crashes or unexpected exceptions
            print(f"[{now()}] 🔥 Exception in main loop: {e}")
            traceback.print_exc()
            crash_count += 1
            # attempt to close driver cleanly
            try:
                if driver:
                    driver.quit()
            except:
                pass
            driver = None
            backoff = BACKOFF_BASE * (2 ** min(crash_count - 1, 6))
            print(f"[{now()}] Restarting driver after {backoff}s backoff (crash #{crash_count})")
            time.sleep(backoff)
            # continue -> new driver created in next loop

if __name__ == "__main__":
    cfg = load_config(CFG_PATH)
    run_forever(cfg)
