import yaml
import random
import time
import sys
import shutil
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ====================
# CONFIG LOADING
# ====================
def load_config(path="ctr.yaml"):
    try:
        with open(path, "r") as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"❌ Failed to load config {path}: {e}")
        sys.exit(1)


# ====================
# DRIVER SETUP
# ====================
def get_driver(headless=True, proxy=None, driver_path=None):
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

    if not driver_path:
        driver_path = shutil.which("chromedriver")
        if not driver_path:
            print("❌ Could not find chromedriver in PATH. Please install it or set driver_path manually.")
            sys.exit(1)
    try:
        service = Service(driver_path)
        driver = webdriver.Chrome(service=service, options=options)
        return driver
    except Exception as e:
        print(f"❌ Failed to start ChromeDriver: {e}")
        sys.exit(1)


# ====================
# CLICK ASIN FUNCTION
# ====================
def click_asin(driver, asin, max_attempts=2):
    """
    Finds and clicks the ASIN link in Amazon search results.
    Returns True if clicked, False otherwise.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            xpath = f'//a[contains(@href, "/dp/{asin}") or contains(@href, "/gp/product/{asin}")]'
            element = WebDriverWait(driver, 6).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            driver.execute_script("arguments[0].scrollIntoView();", element)
            time.sleep(random.uniform(0.8, 1.5))
            element.click()
            print(f"✅ Clicked ASIN: {asin}")
            return True
        except Exception:
            print(f"❌ ASIN {asin} not found. Attempt {attempt}/{max_attempts}")
            time.sleep(random.uniform(1, 2))

    print(f"⚠️ ASIN {asin} skipped after {max_attempts} attempts")
    return False


# ====================
# DEGRADE SEARCH RANK
# ====================
def perform_degrade(driver, keyword, asin_list):
    search_url = f"https://www.amazon.in/s?k={keyword.replace(' ', '+')}"
    driver.get(search_url)

    for asin in asin_list:
        print(f"\n🔍 Searching for keyword: {keyword}")
        browse_time = random.uniform(5, 10)
        print(f"⌛ Browsing search page for {browse_time:.2f} seconds...")
        time.sleep(browse_time)

        clicked = click_asin(driver, asin)
        if clicked:
            stay_time = random.uniform(1.5, 3.5)
            print(f"↩️ Staying {stay_time:.2f}s on ASIN {asin} before going back...")
            time.sleep(stay_time)

            try:
                driver.back()
                # Wait until search bar confirms the page is back
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.ID, "twotabsearchtextbox"))
                )
                # Small scroll + wait to make DOM stable
                driver.execute_script("window.scrollBy(0, 300);")
                time.sleep(random.uniform(2, 4))
                print(f"🔄 Back on search page for keyword: {keyword}")
            except Exception:
                print("⚠️ Could not go back, reloading search page instead")
                driver.get(search_url)
                time.sleep(random.uniform(3, 5))


# ====================
# MAIN FUNCTION
# ====================
def main():
    config = load_config()

    proxies = config.get("proxies", [])
    proxy = random.choice(proxies) if proxies else None
    print("🌐 Using proxy:", proxy or "None")

    driver = get_driver(proxy=proxy, headless=False)  # set headless=True for background runs

    try:
        for keyword, asin_list in config.get("asin_map", {}).items():
            perform_degrade(driver, keyword, asin_list)
    finally:
        driver.quit()
        print("🔒 Driver closed.")


if __name__ == "__main__":
    main()
