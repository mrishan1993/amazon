#!/usr/bin/env python3
import yaml
import random
import time
import sys
import traceback
import csv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from pathlib import Path
import datetime

KEYWORDS_PATH = "keywords.yaml"
EMAIL_CONFIG_PATH = "email.yaml"
FAILED_CSV = "failed_asins.csv"

# Toggle this while debugging locally
HEADLESS = False    # set True for EC2 after debugging
# set a realistic UA
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/121.0.0.0 Safari/537.36")

asin_fail_count = {}

# --------- HELPERS ---------
def log(msg):
    ts = time.strftime("[%Y-%m-%d %H:%M:%S]")
    print(f"{ts} {msg}")
    sys.stdout.flush()

def load_yaml(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)

def get_driver(headless=True, proxy=None):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    # common flags
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(f"--user-agent={USER_AGENT}")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=en-US")
    # optional proxy
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")
    return webdriver.Chrome(service=Service(), options=options)

def ensure_csv(path):
    p = Path(path)
    if not p.exists():
        with p.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["asin", "keyword", "ts", "note"])

def send_email_with_attachment(subject, body, filepath):
    cfg = load_yaml(EMAIL_CONFIG_PATH)["email"]
    msg = MIMEMultipart()
    msg["From"] = cfg["from"]
    msg["To"] = ", ".join(cfg["to"])
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    with open(filepath, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={Path(filepath).name}")
        msg.attach(part)
    try:
        server = smtplib.SMTP(cfg["smtp_server"], cfg["smtp_port"])
        server.starttls()
        server.login(cfg["from"], cfg["password"])
        server.sendmail(cfg["from"], cfg["to"], msg.as_string())
        server.quit()
        log(f"📧 Email sent with {filepath}")
    except Exception as e:
        log(f"⚠️ Failed to send email: {e}")

def record_failed_asin(asin, keyword, note):
    ensure_csv(FAILED_CSV)
    with open(FAILED_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([asin, keyword, time.strftime("%Y-%m-%d %H:%M:%S"), note])
    try:
        send_email_with_attachment("ASIN Fail Report", f"ASIN {asin} failed 10 times", FAILED_CSV)
    except Exception as e:
        log("⚠ failed to send fail-report email: " + str(e))

# --------- SPONSORED DETECTION + CLICK ---------
def save_debug_snapshot(driver, keyword):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    html_file = f"debug_{keyword.replace(' ','_')}_{ts}.html"
    png_file = f"debug_{keyword.replace(' ','_')}_{ts}.png"
    try:
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        driver.get_screenshot_as_file(png_file)
        log(f"🔍 Saved debug files: {html_file}, {png_file}")
    except Exception as e:
        log("⚠ Failed to save debug snapshot: " + str(e))

def find_sponsored_tiles(driver, keyword, max_scrolls=5):
    try:
        WebDriverWait(driver, 12).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.s-main-slot"))
        )
    except Exception:
        pass

    for i in range(max_scrolls):
        driver.execute_script("window.scrollBy(0, window.innerHeight);")
        time.sleep(random.uniform(0.7, 1.5))

    xpath = ("//div[@data-asin and string-length(normalize-space(@data-asin))>0"
             " and .//*[contains(translate(normalize-space(.), "
             "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'sponsored')]]")

    tiles = driver.find_elements(By.XPATH, xpath)
    return tiles

def click_sponsored(driver, keyword):
    search_url = f"https://www.amazon.in/s?k={keyword.replace(' ', '+')}"
    driver.get(search_url)
    time.sleep(random.uniform(2.5, 5.5))  # browsing delay
    log(f"⌛ Browsed search for '{keyword}'")

    tiles = []
    try:
        tiles = find_sponsored_tiles(driver, keyword)
        log(f"🌟 Found {len(tiles)} sponsored products for '{keyword}'")
    except Exception as e:
        log(f"⚠ Error finding sponsored products: {e}")

    if not tiles:
        log("⚠ No sponsored tiles found — saving debug snapshot.")
        save_debug_snapshot(driver, keyword)
        return

    for idx, tile in enumerate(tiles, start=1):
        try:
            asin = tile.get_attribute("data-asin") or ""
            title = ""
            try:
                title_elem = tile.find_element(By.XPATH, ".//h2//span")
                title = title_elem.text or ""
            except Exception:
                try:
                    t2 = tile.find_element(By.XPATH, ".//span[contains(@class,'a-size-base') or contains(@class,'a-size-medium')]")
                    title = t2.text or ""
                except Exception:
                    title = ""

            title_l = title.lower() if title else ""

            if "chamak" in title_l:
                log(f"⛔ Skipping asin {asin} because title contains 'chamak'")
                continue
            if asin.strip() == "B0F7FQSS12":
                log(f"⛔ Skipping blocked ASIN {asin}")
                continue

            link_candidates = tile.find_elements(By.XPATH, ".//a[contains(@href,'/dp/') or contains(@href,'/gp/') or contains(@href,'/sspa/click')]")
            if not link_candidates:
                log(f"⚠ No link found for ASIN {asin}, skipping")
                continue

            link_elem = link_candidates[0]
            try:
                driver.execute_script("arguments[0].removeAttribute('target')", link_elem)
            except Exception:
                pass

            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'})", tile)
            except Exception:
                pass
            time.sleep(random.uniform(0.5, 1.2))

            before_tabs = driver.window_handles
            try:
                link_elem.click()
            except Exception:
                try:
                    driver.execute_script("arguments[0].click();", link_elem)
                except Exception:
                    raise

            log(f"✅ Clicked ASIN {asin} ({idx}/{len(tiles)})")

            after_tabs = driver.window_handles
            if len(after_tabs) > len(before_tabs):
                new_tab = [t for t in after_tabs if t not in before_tabs][0]
                try:
                    driver.switch_to.window(new_tab)
                    # dwell on PDP
                    time.sleep(random.uniform(2.5, 5.0))
                    driver.close()
                except Exception:
                    pass
                try:
                    driver.switch_to.window(before_tabs[0])
                except Exception:
                    driver.get(search_url)
                log(f"🗑 Closed new tab for ASIN {asin}")

            asin_fail_count[asin] = 0
            # simulate user dwell time
            time.sleep(random.uniform(2.0, 4.5))

            try:
                driver.back()
                WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.s-main-slot")))
                time.sleep(random.uniform(1.0, 2.0))
            except Exception:
                log("⚠ Could not go back reliably; reloading search results page")
                driver.get(search_url)
                time.sleep(random.uniform(2.0, 3.0))

        except Exception as e:
            log(f"❌ Failed ASIN click loop item: {e}")
            try:
                asin = tile.get_attribute("data-asin")
            except Exception:
                asin = ""
            asin_fail_count[asin] = asin_fail_count.get(asin, 0) + 1
            if asin and asin_fail_count[asin] >= 10:
                record_failed_asin(asin, keyword, "10 consecutive failures")
                asin_fail_count[asin] = 0
            continue

# --------- MAIN LOOP ---------
def main():
    keywords_cfg = load_yaml(KEYWORDS_PATH)
    keywords = keywords_cfg.get("keywords", [])

    while True:
        driver = None
        try:
            driver = get_driver(headless=HEADLESS)
            for keyword in keywords:
                click_sponsored(driver, keyword)
                # pause between keywords
                time.sleep(random.uniform(5.0, 10.0))
            driver.quit()
            log("🔒 Driver closed, restarting loop")
            time.sleep(random.uniform(12, 25))
        except Exception as e:
            log("💥 CRASH: " + str(e))
            traceback.print_exc()
            try:
                if driver:
                    driver.quit()
            except:
                pass
            log("🔄 Restarting after crash in 15s...")
            time.sleep(15)

if __name__ == "__main__":
    main()
