import requests
from bs4 import BeautifulSoup
import yaml
import time

# Load config from YAML
with open("keywords_car_body_polish.yaml", "r") as f:
    config = yaml.safe_load(f)

ASIN = config["asin"]
DOMAIN = config["domain"]
KEYWORDS = config["keywords"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/105.0.0.0 Safari/537.36"
    )
}

# ---------- Function: Get BSR ----------
def get_bsr(asin):
    url = f"{DOMAIN}/dp/{asin}"
    r = requests.get(url, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")

    bsr_element = soup.find("span", string=lambda t: t and "Best Sellers Rank" in t)
    if bsr_element:
        rank_text = bsr_element.find_next("span").get_text(strip=True)
        return rank_text
    else:
        return "BSR not found"

# ---------- Function: Get Keyword Rank ----------
def get_keyword_rank(asin, keyword, max_pages=5):
    for page in range(1, max_pages + 1):
        url = f"{DOMAIN}/s?k={keyword}&page={page}"
        r = requests.get(url, headers=HEADERS)
        soup = BeautifulSoup(r.text, "html.parser")

        results = soup.find_all("div", {"data-asin": True})
        for idx, item in enumerate(results, start=1):
            if item["data-asin"] == asin:
                return f"Page {page}, Position {idx}"
        time.sleep(2)  # rate-limit to avoid block
    return f"Not found in top {max_pages*20} results"

# ---------- Main ----------
if __name__ == "__main__":
    print(f"Fetching data for ASIN {ASIN}...\n")

    # Get BSR
    bsr = get_bsr(ASIN)
    print(f"Best Sellers Rank: {bsr}\n")

    # Get Search Ranks
    for kw in KEYWORDS:
        rank = get_keyword_rank(ASIN, kw)
        print(f"Keyword: '{kw}' → {rank}")
