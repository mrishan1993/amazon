# main.py
import yaml
from scraper import polite_fetch_and_parse
from nlp_utils import extract_candidate_keywords, semantic_cluster
from sentiment_utils import aggregate_review_sentiment, extract_mentioned_features
from scorer import combine_keyword_frequencies, top_keywords_by_effectiveness
from reporter import save_asin_keyword_csv, save_aggregated_keywords_csv, save_feature_sentiment_csv
from aggregator import aggregate_keyword_insights
from emailer import send_email
import os
from typing import Dict

CONFIG_PATH = "competrack/config/competitors.yaml"
EMAIL_CONFIG = "competrack/config/email.yaml"

def load_config(path: str = CONFIG_PATH):
    with open(path, "r") as f:
        return yaml.safe_load(f)

def build_per_asin_keyword_map(scraped_data: Dict[str, Dict], top_k: int = 40):
    per_asin = {}
    for asin, data in scraped_data.items():
        combined_text = " ".join([data.get("title",""), data.get("bullets",""), data.get("description",""), data.get("enhanced","")])
        kws = extract_candidate_keywords(combined_text, top_k=top_k)
        per_asin[asin] = {phrase: score for phrase, score in kws}
    return per_asin

def run():
    cfg = load_config()
    asins = cfg.get("asins", [])
    marketplace = cfg.get("marketplace", "IN")
    print("[main] Fetching listing pages...")
    scraped = polite_fetch_and_parse(asins, marketplace)
    print("[main] Extracting keywords per ASIN...")
    per_asin_map = build_per_asin_keyword_map(scraped, top_k=60)

    # Save per-ASIN CSVs & compute features
    csv_attachments = []
    for asin, kwmap in per_asin_map.items():
        sorted_kws = sorted(kwmap.items(), key=lambda x: x[1], reverse=True)
        csv_path = save_asin_keyword_csv(asin, sorted_kws)
        csv_attachments.append(csv_path)

        # sentiment / feature extraction
        reviews = scraped.get(asin, {}).get("reviews", [])
        feature_map = extract_mentioned_features(reviews, top_n=25)
        feat_csv = save_feature_sentiment_csv(asin, feature_map)
        csv_attachments.append(feat_csv)

    # Combined scoring
    combined = combine_keyword_frequencies(per_asin_map)
    agg_csv = save_aggregated_keywords_csv(combined)
    csv_attachments.append(agg_csv)

    # Compute top keywords for immediate recommendations (top 20)
    top_kw = top_keywords_by_effectiveness(combined, top_n=20)
    # Build a brief recommendations text
    recs = []
    for kw, meta in top_kw:
        recs.append(f"{kw} (effectiveness={meta['effectiveness']:.3f}, occurrence={meta['occurrence']})")
    rec_text = "Top recommended keywords to prioritize:\n" + "\n".join(recs)

    
    print("[main] Aggregating all keyword insights...")
    aggregate_keyword_insights(results_folder="results")
    # Email results
    subject = "CompetiTrack Weekly Reports"
    body = "Attached are the latest competitor keyword and feature-sentiment reports.\n\n" + rec_text
    send_email(subject, body, csv_attachments, config_path=EMAIL_CONFIG)
    print("[main] Done. Reports generated and emailed.")

if __name__ == "__main__":
    run()
