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

CONFIG_PATH = "ctr.yaml"
EMAIL_CONFIG_PATH = "email.yaml"
FAILED_CSV = "failed_asins.csv"

asin_fail_count = {}

# ========== HELPERS ==========

def log(msg):
    ts = time.strftime("[%Y-%m-%d %H:%M:%S]")
    print(f"{ts} {msg}")
    sys.stdout.flush()

def load_config(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)

def get_driver(headless=True, proxy=None):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")
    return webdriver.Chrome(service=Service(), options=options)

def ensure_csv(path):
    """Create CSV with header if not exists."""
    try:
        with open(path, "x", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["asin", "keyword", "ts", "note"])
    except FileExistsError:
        pass

def send_email_with_attachment(subject, body, filepath):
    cfg = load_config(EMAIL_CONFIG_PATH)["email"]

    msg = MIMEMultipart()
    msg["From"] = cfg["from"]
    msg["To"] = ", ".join(cfg["to"])
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "plain"))

    with open(filepath, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={filepath}")
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

# ========== CORE ACTIONS ==========

def record_failed_asin(asin, keyword, note):
    ensure_csv(FAILED_CSV)
    with open(FAILED_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([asin, keyword, time.strftime("%Y-%m-%d %H:%M:%S"), note])
    send_email_with_attachment("ASIN Fail Report", f"ASIN {asin} failed 10 times.", FAILED_CSV)

def click_asin(driver, keyword, asin, attempts=3):
    """
    Try to click an ASIN product link from the search results.
    Ensures it opens in the SAME tab, and closes extra tabs if any.
    """
    for attempt in range(1, attempts + 1):
        try:
            elem = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, f"//a[@href and contains(@href,'/dp/{asin}')]"))
            )

            # Remove 'target' attr so it doesn't open a new tab
            driver.execute_script("arguments[0].removeAttribute('target')", elem)

            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
            time.sleep(0.5)

            before_tabs = driver.window_handles
            elem.click()
            log(f"✅ Clicked anchor for ASIN {asin} (attempt {attempt})")

            after_tabs = driver.window_handles
            if len(after_tabs) > len(before_tabs):
                new_tab = [t for t in after_tabs if t not in before_tabs][0]
                driver.switch_to.window(new_tab)
                driver.close()
                driver.switch_to.window(before_tabs[0])
                log(f"🗑 Closed unwanted new tab for ASIN {asin}")

            # reset fail counter on success
            asin_fail_count[asin] = 0
            return True
        except Exception as e:
            log(f"❌ Could not click ASIN {asin} (attempt {attempt}): {e}")
            time.sleep(2)

    # Failed after all attempts
    asin_fail_count[asin] = asin_fail_count.get(asin, 0) + 1
    if asin_fail_count[asin] >= 10:
        record_failed_asin(asin, keyword, "10 consecutive failures")
        asin_fail_count[asin] = 0
    return False

def perform_degrade(driver, keyword, asin_list):
    search_url = f"https://www.amazon.in/s?k={keyword.replace(' ', '+')}"
    driver.get(search_url)
    browse_time = random.uniform(5, 10)
    log(f"⌛ Browsed search for '{keyword}' ~{browse_time:.1f}s")
    time.sleep(browse_time)

    for asin in asin_list:
        if not click_asin(driver, keyword, asin):
            log(f"⚠ SKIPPING ASIN {asin} (click failed)")
            continue

        stay_time = random.uniform(3, 6)
        log(f"↩️ Staying {stay_time:.1f}s on ASIN {asin}")
        time.sleep(stay_time)

        try:
            driver.back()
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "twotabsearchtextbox"))
            )
            time.sleep(random.uniform(2, 4))
            log(f"🔄 Returned to search results for '{keyword}'")
        except:
            log("⚠ Could not navigate back, reloading search page")
            driver.get(search_url)
            time.sleep(random.uniform(3, 5))

# ========== MAIN LOOP ==========

def main():
    while True:
        driver = None
        try:
            config = load_config(CONFIG_PATH)
            asin_map = config.get("asin_map", {})
            if not asin_map:
                log("❌ No asin_map found in config")
                time.sleep(30)
                continue

            proxies = config.get("proxies", [])
            proxy = random.choice(proxies) if proxies else None
            log("🌐 Using proxy: " + (proxy or "None"))

            driver = get_driver(headless=True, proxy=proxy)

            for keyword, asin_list in asin_map.items():
                perform_degrade(driver, keyword, asin_list)

            driver.quit()
            log("🔒 Driver closed, restarting loop")
            time.sleep(5)

        except Exception as e:
            log("💥 CRASH: " + str(e))
            traceback.print_exc()
            try:
                if driver:
                    driver.quit()
            except:
                pass
            log("🔄 Restarting after crash in 10s...")
            time.sleep(10)

if __name__ == "__main__":
    main()
