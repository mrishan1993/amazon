import os
import pandas as pd
from datetime import datetime
from typing import Dict, List
from keyword_expander import get_related_keywords
from trend_utils import get_trend_score


OUTPUT_DIR = "reports"
RESULTS_DIR = "results"
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)


def save_asin_keyword_csv(asin: str, keywords: List[tuple]) -> str:
    """
    Save extracted keywords for a single ASIN.
    :param asin: product ASIN
    :param keywords: list of tuples (keyword, score)
    """
    df = pd.DataFrame(keywords, columns=["keyword", "score"])
    filename = os.path.join(
        OUTPUT_DIR, f"{asin}_keywords_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.csv"
    )
    df.to_csv(filename, index=False)
    print(f"[reporter] Saved keyword CSV for {asin} -> {filename}")
    return filename


def save_feature_sentiment_csv(asin: str, feature_map: Dict) -> str:
    """
    Save feature sentiment summary per ASIN.
    """
    rows = []
    for feature, data in feature_map.items():
        rows.append({
            "feature": feature,
            "mention_count": data.get("count", 0),
            "avg_sentiment": data.get("sentiment", 0.0)
        })

    if not rows:
        rows = [{"feature": "N/A", "mention_count": 0, "avg_sentiment": 0.0}]

    df = pd.DataFrame(rows).sort_values(by="mention_count", ascending=False)
    file_path = os.path.join(RESULTS_DIR, f"{asin}_features.csv")
    df.to_csv(file_path, index=False)
    print(f"[reporter] Saved feature sentiment CSV for {asin} -> {file_path}")
    return file_path


def aggregate_keyword_data(all_keywords: Dict[str, Dict[str, Dict]]) -> Dict[str, Dict]:
    """
    Combine all ASIN keyword maps into a single dictionary.
    Input format: {asin: {keyword: {"score": float, "sentiment": float}}}
    """
    combined = {}

    for asin, kw_map in all_keywords.items():
        for keyword, data in kw_map.items():
            if keyword not in combined:
                combined[keyword] = {
                    "total_score": 0.0,
                    "occurrence": 0,
                    "asin_count": 0,
                    "sentiment_sum": 0.0,
                    "asins": set()
                }
            combined[keyword]["total_score"] += data.get("score", 0.0)
            combined[keyword]["occurrence"] += 1
            combined[keyword]["sentiment_sum"] += data.get("sentiment", 0.0)
            combined[keyword]["asins"].add(asin)

    # Calculate averages
    for kw, v in combined.items():
        v["asin_count"] = len(v["asins"])
        v["avg_sentiment"] = (
            v["sentiment_sum"] / v["asin_count"] if v["asin_count"] > 0 else 0.0
        )
        v["normalized_score"] = v["total_score"] / v["occurrence"] if v["occurrence"] else 0.0

    return combined


def enrich_keyword_data(keyword: str) -> Dict[str, str]:
    """
    Add external enrichment using Amazon Autocomplete + Google Trends.
    """
    try:
        related = get_related_keywords(keyword)
    except Exception:
        related = []

    try:
        trend = get_trend_score(keyword)
    except Exception:
        trend = 0.0

    return {
        "related_keywords": ", ".join(related) if related else "",
        "trend_growth": trend
    }


def compute_effectiveness(row: dict) -> float:
    """
    Weighted scoring:
    - Mention frequency → 40%
    - Sentiment positivity → 25%
    - ASIN coverage → 25%
    - Trend growth → 10%
    """
    freq_score = min(row.get("occurrence", 0) / 10, 1.0)
    sentiment_score = (row.get("avg_sentiment", 0) + 1) / 2  # normalize -1 → 1
    asin_coverage = min(row.get("asin_count", 0) / 5, 1.0)
    trend_score = max(row.get("trend_growth", 0), 0) / 100  # normalize %

    effectiveness = (
        (0.4 * freq_score) +
        (0.25 * sentiment_score) +
        (0.25 * asin_coverage) +
        (0.1 * trend_score)
    )
    return round(effectiveness * 100, 2)  # percentage


def save_aggregated_keywords_csv(combined: Dict[str, Dict]) -> str:
    """
    Merge, enrich, compute effectiveness and save master keyword CSV.
    """
    rows = []

    print("[reporter] Enriching and scoring keywords...")

    for k, v in combined.items():
        enrich = enrich_keyword_data(k)
        trend_growth = enrich.get("trend_growth", 0)
        eff = compute_effectiveness({
            **v,
            "trend_growth": trend_growth
        })

        rows.append({
            "keyword": k,
            "occurrence": v["occurrence"],
            "asin_count": v["asin_count"],
            "avg_sentiment": round(v["avg_sentiment"], 3),
            "normalized_score": round(v["normalized_score"], 3),
            "trend_growth": trend_growth,
            "related_keywords": enrich.get("related_keywords", ""),
            "effectiveness": eff
        })

    df = pd.DataFrame(rows).sort_values(by="effectiveness", ascending=False)
    filename = os.path.join(
        OUTPUT_DIR, f"final_keywords_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.csv"
    )
    df.to_csv(filename, index=False)
    print(f"[reporter] ✅ Saved enriched keyword CSV -> {filename}")
    return filename


def summarize_top_keywords(df: pd.DataFrame, top_n=10) -> str:
    """
    Generate a human-readable summary for email or dashboard.
    """
    summary = ["Top Keywords Summary:"]
    for _, row in df.head(top_n).iterrows():
        summary.append(
            f"- {row['keyword']}: effectiveness {row['effectiveness']}%, "
            f"sentiment {row['avg_sentiment']}, used in {row['asin_count']} ASINs, "
            f"trend +{row['trend_growth']}%"
        )
    return "\n".join(summary)
