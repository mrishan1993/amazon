# scorer.py
from collections import Counter
from typing import Dict, List
import math

def combine_keyword_frequencies(per_asin_keywords: Dict[str, Dict[str, float]]) -> Dict[str, Dict]:
    """
    per_asin_keywords: {asin: {keyword: score}}
    Returns combined metrics per keyword: occurrence_count, total_score, normalized_score
    """
    keyword_counts = Counter()
    keyword_totals = Counter()
    for asin, kws in per_asin_keywords.items():
        for k, score in kws.items():
            keyword_counts[k] += 1
            keyword_totals[k] += score
    combined = {}
    max_total = max(keyword_totals.values()) if keyword_totals else 1.0
    for k in keyword_totals:
        occurrence = keyword_counts[k]
        total = keyword_totals[k]
        # simplified dynamic effectiveness: occurrence weight + normalized total tfidf-like score
        norm = total / max_total
        effectiveness = (math.log(1 + occurrence) * 0.6) + (norm * 0.4)
        combined[k] = {
            "occurrence": occurrence,
            "raw_total_score": total,
            "normalized_total": norm,
            "effectiveness": effectiveness
        }
    return combined

def top_keywords_by_effectiveness(combined: Dict[str, Dict], top_n: int = 50):
    ranked = sorted(combined.items(), key=lambda x: x[1]["effectiveness"], reverse=True)[:top_n]
    return ranked
