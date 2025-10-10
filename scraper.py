# scraper.py
import requests
from bs4 import BeautifulSoup
import time
import random
from typing import List, Dict, Optional

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

def fetch_product_page(asin: str, marketplace: str = "IN", timeout: int = 15, proxies: Optional[Dict] = None) -> Optional[str]:
    url = f"https://www.amazon.{marketplace.lower()}/dp/{asin}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, proxies=proxies)
        if resp.status_code == 200:
            return resp.text
        else:
            print(f"[scraper] Non-200 for {asin}: {resp.status_code}")
            return None
    except Exception as e:
        print(f"[scraper] Error fetching {asin}: {e}")
        return None

def parse_listing(html: str) -> Dict:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find(id="productTitle")
    title = title_tag.get_text(strip=True) if title_tag else ""
    bullets = soup.select("#feature-bullets ul li span")
    bullet_text = " ".join([b.get_text(strip=True) for b in bullets])
    desc_tag = soup.find(id="productDescription")
    description = desc_tag.get_text(strip=True) if desc_tag else ""
    # Try additional content blocks
    enhanced_bullets = ""
    for div in soup.select("div#aplus_content") + soup.select(".a-section.a-spacing-small"):
        enhanced_bullets += " " + div.get_text(separator=" ", strip=True)
    # Reviews (first page)
    reviews = []
    review_blocks = soup.select(".review-text-content span")
    for r in review_blocks[:10]:
        reviews.append(r.get_text(strip=True))
    return {
        "title": title,
        "bullets": bullet_text,
        "description": description,
        "enhanced": enhanced_bullets,
        "reviews": reviews
    }

def polite_fetch_and_parse(asins: List[str], marketplace: str = "IN", sleep_range=(2,5)) -> Dict[str, Dict]:
    results = {}
    for asin in asins:
        html = fetch_product_page(asin, marketplace)
        if not html:
            results[asin] = {}
            continue
        data = parse_listing(html)
        results[asin] = data
        # polite sleep
        time.sleep(random.uniform(*sleep_range))
    return results

if __name__ == "__main__":
    # quick test (not run in this file)
    pass
