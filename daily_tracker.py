import yaml
import random
import time
import pandas as pd
import smtplib
from email.message import EmailMessage
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# ----------------------------
# Load config
# ----------------------------
def load_config(path="track.yaml"):
    with open(path, "r") as f:
        return yaml.safe_load(f)

# ----------------------------
# Setup Chrome driver
# ----------------------------
def get_driver(proxy=None, headless=True):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    if proxy:
        options.add_argument(f"--proxy-server={proxy}")

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30)
    return driver

# ----------------------------
# Scrape metrics for an ASIN
# ----------------------------
def scrape_asin(driver, asin):
    product_url = f"https://www.amazon.in/dp/{asin}"
    print(f"🔗 Opening product page for ASIN: {asin}")
    try:
        driver.get(product_url)
        time.sleep(random.uniform(3, 6))
    except Exception as e:
        print(f"❌ Failed to open ASIN page: {asin} — {e}")
        return {}

    data = {"asin": asin}

    # BSR
    try:
        bsr_elem = driver.find_element(By.XPATH, "//span[contains(text(),'Best Sellers Rank')]/following-sibling::span")
        data["bsr"] = bsr_elem.text
    except:
        data["bsr"] = None

    # Price
    try:
        price_elem = driver.find_element(By.ID, "priceblock_ourprice")
        data["price"] = price_elem.text
    except:
        data["price"] = None

    # Rating
    try:
        rating_elem = driver.find_element(By.XPATH, "//span[@data-hook='rating-out-of-text']")
        data["rating"] = rating_elem.text
    except:
        data["rating"] = None

    # Review count
    try:
        review_elem = driver.find_element(By.ID, "acrCustomerReviewText")
        data["review_count"] = review_elem.text
    except:
        data["review_count"] = None

    return data

# ----------------------------
# Find search rank for ASIN by keyword
# ----------------------------
def get_search_rank(driver, keyword, asin, max_pages=5):
    print(f"🔍 Searching for keyword '{keyword}' to find ASIN {asin}")
    for page in range(1, max_pages + 1):
        search_url = f"https://www.amazon.in/s?k={keyword.replace(' ', '+')}&page={page}"
        try:
            driver.get(search_url)
            time.sleep(random.uniform(4, 8))
        except Exception as e:
            print(f"❌ Failed to open search page: {e}")
            return None

        try:
            elements = driver.find_elements(By.XPATH, f'//a[contains(@href, "{asin}")]')
            if elements:
                print(f"✅ ASIN {asin} found on page {page}")
                return (page - 1) * 16 + 1  # Approx rank (16 results per page)
        except Exception as e:
            print(f"⚠️ Error finding ASIN {asin} on page {page}: {e}")

    print(f"❌ ASIN {asin} not found in first {max_pages} pages")
    return None

# ----------------------------
# Send email with CSV attachment
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
        with smtplib.SMTP(config["tracking"]["email"]["smtp_server"], config["tracking"]["email"]["smtp_port"]) as server:
            server.starttls()
            server.login(config["tracking"]["email"]["from"], config["tracking"]["email"]["password"])
            server.send_message(msg)
        print("✅ Email sent successfully")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")

# ----------------------------
# Main
# ----------------------------
def main():
    config = load_config()

    proxies = []  # Add proxy list if needed
    proxy = random.choice(proxies) if proxies else None

    driver = get_driver(proxy=proxy, headless=False)

    all_results = []

    for keyword, asins in config["tracking"]["keywords_asins"].items():
        for asin in asins:
            asin_data = scrape_asin(driver, asin)
            asin_data["keyword"] = keyword
            asin_data["search_rank"] = get_search_rank(driver, keyword, asin)
            all_results.append(asin_data)
            time.sleep(random.uniform(3, 6))

    driver.quit()

    # Save CSV
    df = pd.DataFrame(all_results)
    csv_file = "daily_amazon_tracking.csv"
    df.to_csv(csv_file, index=False)
    print(f"💾 CSV saved as {csv_file}")

    # Send email
    send_email(config, csv_file)


if __name__ == "__main__":
    main()
