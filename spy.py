import requests, yaml, time
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import pandas as pd
from datetime import datetime

def get_amazon_url(asin, marketplace="IN"):
    domain = "amazon.in" if marketplace == "IN" else "amazon.com"
    return f"https://www.{domain}/dp/{asin}"

def get_html(url):
    headers = {'User-Agent': UserAgent().random}
    res = requests.get(url, headers=headers)
    if res.status_code != 200:
        print(f"❌ Failed to fetch {url}")
    return res.text

def parse_listing(html):
    soup = BeautifulSoup(html, 'html.parser')

    def safe_find_text(tag, attrs=None):
        found = soup.find(tag, attrs=attrs)
        return found.get_text(strip=True) if found else None

    # Title
    title = safe_find_text("span", {"id": "productTitle"})

    # Price (choose best guess)
    price = safe_find_text("span", {"class": "a-price-whole"})
    if not price:
        price = safe_find_text("span", {"class": "a-offscreen"})

    # Rating
    rating = safe_find_text("span", {"class": "a-icon-alt"})

    # Review count
    review_count = safe_find_text("span", {"id": "acrCustomerReviewText"})

    # Bullets
    bullet_list = soup.select("#feature-bullets ul li span")
    bullets = [b.get_text(strip=True) for b in bullet_list if b.get_text(strip=True)]

    # Image URL
    img_tag = soup.find("img", {"id": "landingImage"})
    image_url = img_tag["src"] if img_tag else None
    print("title:", title)
    print("price:", price)
    print("rating:", rating)
    print("review_count:", review_count)
    print("bullets:", bullets)
    print("image_url:", image_url)
    print("\n")

    return {
        "title": title,
        "price": price,
        "rating": rating,
        "review_count": review_count,
        "bullets": " | ".join(bullets),
        "image_url": image_url,
    }

def load_asins(filepath="competitors.yaml"):
    with open(filepath, "r") as f:
        return yaml.safe_load(f)

def save_to_csv(data):
    df = pd.DataFrame(data)
    date_str = datetime.now().strftime("%Y-%m-%d")
    df.to_csv(f"output/{date_str}_competitor_data.csv", index=False)
    print(f"✅ Data saved for {len(data)} ASINs")

if __name__ == "__main__":
    config = load_asins()
    asin_list = config['asins']
    marketplace = config.get("marketplace", "IN")

    results = []

    for asin in asin_list:
        url = get_amazon_url(asin, marketplace)
        print(f"🔍 Scraping {asin} ...")
        html = get_html(url)
        listing = parse_listing(html)
        listing["asin"] = asin
        listing["url"] = url
        listing["scraped_at"] = datetime.now().isoformat()
        results.append(listing)
        time.sleep(2)  # be kind to Amazon's servers

    save_to_csv(results)
