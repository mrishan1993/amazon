import requests, csv, yaml, os
from bs4 import BeautifulSoup
from datetime import datetime
from fake_useragent import UserAgent

def get_headers():
    ua = UserAgent()
    return {
        "User-Agent": ua.random,
        "Accept-Language": "en-IN,en;q=0.9"
    }

def extract_data(asin):
    url = f"https://www.amazon.in/dp/{asin}"
    response = requests.get(url, headers=get_headers())
    soup = BeautifulSoup(response.content, "html.parser")

    def safe_text(selector):
        el = soup.select_one(selector)
        return el.get_text(strip=True) if el else "N/A"

    data = {
        "asin": asin,
        "title": safe_text("#productTitle"),
        "star_rating": safe_text("span[data-asin-rating]") or safe_text("span.a-icon-alt"),
        "review_count": safe_text("#acrCustomerReviewText"),
        "top_review_title": safe_text(".review-title span"),
        "top_review_snippet": safe_text(".review-text-content span"),
        "bsr": "N/A"
    }

    # BSR is found in the 'Product details' section
    bsr_section = soup.find(id="detailBulletsWrapper_feature_div") or soup.find(id="prodDetails")
    if bsr_section:
        text = bsr_section.get_text(" ", strip=True)
        for line in text.split("#")[1:]:
            rank_info = line.split(" ")[0]
            if rank_info.isdigit():
                data["bsr"] = "#" + rank_info
                break
    return data

# Load config
with open("rating.config.yaml", "r") as f:
    config = yaml.safe_load(f)

asins = config["asins"]
today = datetime.now().strftime("%Y-%m-%d")
rows = [["date", "asin", "label", "star_rating", "review_count", "bsr", "top_review_title", "top_review_snippet"]]

# Scrape each ASIN
for entry in asins:
    asin = entry["asin"]
    label = entry.get("label", "N/A")
    print(f"Tracking ASIN: {asin} ({label})")
    data = extract_data(asin)
    rows.append([
        today,
        asin,
        label,
        data["star_rating"],
        data["review_count"],
        data["bsr"],
        data["top_review_title"],
        data["top_review_snippet"]
    ])
    print(f"✅ Tracked ASIN: {asin} ({label})")
    print("star_rating:", data["star_rating"])
    print("review_count:", data["review_count"])
    print("bsr:", data["bsr"])
    print("top_review_title:", data["top_review_title"])
    print("top_review_snippet:", data["top_review_snippet"])

# Save CSV
os.makedirs("output", exist_ok=True)
with open(f"output/review_bsr_{today}.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerows(rows)

print(f"✅ Saved to output/review_bsr_{today}.csv")
