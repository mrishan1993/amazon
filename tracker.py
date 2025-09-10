import yaml, csv, time, os
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException

# Load config
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

keywords = config["keywords"]
product_groups = config["products"]
max_pages = config.get("max_pages", 3)
today = datetime.now().strftime("%Y-%m-%d")

# Headless Chrome setup
options = webdriver.ChromeOptions()
options.add_argument("--headless")
driver = webdriver.Chrome(options=options)

rows = [["date", "keyword", "product_group", "label", "asin", "rank"]]

def get_asin_rank(keyword, target_asin):
    for page_num in range(1, max_pages + 1):
        try:
            url = f"https://www.amazon.in/s?k={keyword.replace(' ', '+')}&page={page_num}"
            driver.get(url)
            time.sleep(3)
            items = driver.find_elements(By.CSS_SELECTOR, "[data-asin]")
            for i, item in enumerate(items, start=1):
                found_asin = item.get_attribute("data-asin")
                if found_asin == target_asin:
                    return (page_num - 1) * len(items) + i
        except WebDriverException as e:
            print(f"Error fetching page {page_num} for keyword '{keyword}': {e}")
            return "Error"
    return "Not Found"

# Loop through product groups
for product in product_groups:
    product_label = product["label"]
    main_asin = product["asin"]
    all_asins = [{"asin": main_asin, "label": product_label}] + product["competitors"]

    for keyword in keywords:
        for entry in all_asins:
            asin = entry["asin"]
            label = entry["label"]
            rank = get_asin_rank(keyword, asin)
            print(f"{keyword} | {product_label} | {label} ({asin}): Rank {rank}")
            rows.append([today, keyword, product_label, label, asin, rank])
            time.sleep(2)

# Save output
os.makedirs("output", exist_ok=True)
with open(f"output/rankings_{today}.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerows(rows)

driver.quit()
