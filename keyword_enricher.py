import requests
from bs4 import BeautifulSoup
import pandas as pd
import time, random, os

def fetch_related_keywords(keyword, max_results=15):
    """
    Fetch related keywords for Amazon search via KeywordTool.io (no API key needed).
    Returns a list of suggestion strings.
    """
    try:
        print(f"[keyword_enricher] Fetching related keywords for: {keyword}")
        url = f"https://keywordtool.io/search/keywords/{keyword}/amazon"
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        suggestions = [el.text.strip() for el in soup.select(".results-table .result-item span.text")]
        suggestions = list(dict.fromkeys(suggestions))  # dedupe while preserving order
        return suggestions[:max_results]
    except Exception as e:
        print(f"[keyword_enricher] Failed for {keyword}: {e}")
        return []

def enrich_keywords(base_keywords, output_file="results/enriched_keywords.csv"):
    """
    Expands the current keyword list using KeywordTool.io scraping.
    """
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    rows = []
    for kw in base_keywords:
        suggestions = fetch_related_keywords(kw)
        for s in suggestions:
            rows.append({"base_keyword": kw, "suggested_keyword": s})
        time.sleep(random.uniform(2, 4))
    df = pd.DataFrame(rows)
    df.to_csv(output_file, index=False)
    print(f"[keyword_enricher] ✅ Saved enriched keywords → {output_file}")
    return df
