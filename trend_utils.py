# trend_utils.py
import requests
from bs4 import BeautifulSoup
import time
import random

HEADERS_LIST = [
    # A few user agents to rotate and avoid being blocked
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Firefox/117.0",
]

BASE_URL = "https://www.amazon.in/s"

def fetch_amazon_results_count(keyword):
    """
    Fetches the number of search results for a keyword from Amazon.
    Returns 0 if it fails or keyword is invalid.
    """
    headers = {"User-Agent": random.choice(HEADERS_LIST)}
    params = {"k": keyword}

    try:
        resp = requests.get(BASE_URL, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            return 0

        soup = BeautifulSoup(resp.text, "html.parser")
        # The results count is in span with text like "1-48 of X results"
        results_text = soup.find("span", string=lambda t: t and "results" in t)
        if not results_text:
            return 0

        text = results_text.get_text().replace(",", "")
        # Extract the number before "results"
        import re
        match = re.search(r"of\s+([\d,]+)\s+results", text, re.I)
        if match:
            count = int(match.group(1))
            return count
        return 0
    except Exception as e:
        print(f"[trend_utils] Failed to fetch results for '{keyword}': {e}")
        return 0

def get_trend_score(keyword):
    """
    Returns a normalized trend score (0–100) based on Amazon search results count.
    """
    print(f"[trend_utils] Fetching trend score for: {keyword}")
    count = fetch_amazon_results_count(keyword)
    # Normalize 0-100 (tweak divisor based on typical counts in your niche)
    score = min(count / 1000, 100)
    # Random small jitter to avoid identical scores for low-volume keywords
    score += random.uniform(0, 2)
    score = round(score, 2)
    time.sleep(random.uniform(1, 2))  # polite delay to avoid blocking
    return score
