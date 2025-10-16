import pandas as pd
import os
import numpy as np

def aggregate_keyword_insights(results_folder="results", output_file="results/combined_keyword_insights.csv"):
    """
    Aggregates all extracted keyword data across ASINs into one master CSV.
    Each row = keyword with calculated metrics to identify top-performing terms.
    """

    # Collect all possible keyword CSVs (feature + keyword ones)
    files = [
        f for f in os.listdir(results_folder)
        if f.endswith("_keywords.csv") or f.endswith("_features.csv") or "final_keywords" in f
    ]
    if not files:
        print("[aggregator] ⚠️ No keyword or feature CSVs found in folder.")
        return pd.DataFrame()

    keyword_data = {}

    for f in files:
        asin = f.split("_")[0]
        path = os.path.join(results_folder, f)

        try:
            df = pd.read_csv(path)
        except Exception as e:
            print(f"[aggregator] Skipping {f} due to error: {e}")
            continue

        # Normalize column names
        df.columns = [c.lower().strip() for c in df.columns]

        # Detect the column name used for keywords/features
        kw_col = "keyword" if "keyword" in df.columns else "feature" if "feature" in df.columns else None
        if not kw_col:
            continue

        for _, row in df.iterrows():
            kw = str(row[kw_col]).strip().lower()
            if kw in ("nan", "", "none"):
                continue

            mentions = int(row.get("mention_count", row.get("occurrence", 1)))
            sentiment = float(row.get("avg_sentiment", row.get("sentiment", 0)))
            rating = float(row.get("avg_star_rating", 4.0))
            asin_list = set([asin])

            if kw not in keyword_data:
                keyword_data[kw] = {
                    "total_mentions": 0,
                    "asin_list": set(),
                    "sentiment_sum": 0.0,
                    "rating_sum": 0.0,
                }

            keyword_data[kw]["total_mentions"] += mentions
            keyword_data[kw]["sentiment_sum"] += sentiment
            keyword_data[kw]["rating_sum"] += rating
            keyword_data[kw]["asin_list"].update(asin_list)

    rows = []
    for kw, stats in keyword_data.items():
        asin_count = len(stats["asin_list"])
        avg_sentiment = stats["sentiment_sum"] / max(1, asin_count)
        avg_rating = stats["rating_sum"] / max(1, asin_count)

        # Weighted effectiveness
        effectiveness = (
            (stats["total_mentions"] * 0.4) +
            (asin_count * 1.5) +
            (avg_sentiment * 20) +
            (avg_rating * 10)
        )
        effectiveness = round(np.clip(effectiveness / 10, 0, 10), 2)

        # Simple recommendation logic
        if avg_sentiment < -0.2:
            suggestion, note = "Avoid", "Negative sentiment keyword"
        elif effectiveness > 7:
            suggestion, note = "Add to title", "Strong signal, widely used"
        elif 5 < effectiveness <= 7:
            suggestion, note = "Add to bullets", "Moderate strength keyword"
        else:
            suggestion, note = "Low priority", "Low usage or weak sentiment"

        rows.append({
            "keyword": kw,
            "total_mentions": stats["total_mentions"],
            "asin_count": asin_count,
            "avg_sentiment": round(avg_sentiment, 3),
            "avg_star_rating": round(avg_rating, 2),
            "effectiveness_score": effectiveness,
            "suggestion": suggestion,
            "notes": note,
            "used_in_asins": ", ".join(list(stats["asin_list"]))
        })

        # ✅ Filter only once, after loop finishes
    rows = [row for row in rows if row["asin_count"] > 0]

    df = pd.DataFrame(rows)
    # --- Optional: Keyword enrichment integration ---
    try:
        from keyword_enricher import enrich_keywords
        from keyword_enricher_ext import integrate_with_helium10

        base_keywords = df["keyword"].head(20).tolist()
        enriched_df = enrich_keywords(base_keywords)
        final_enriched = integrate_with_helium10()
        print(f"[aggregator] ✅ Enriched keywords integrated ({len(final_enriched)} total)")
    except Exception as e:
        print(f"[aggregator] ⚠️ Enrichment failed: {e}")

    if df.empty:
        print("[aggregator] ⚠️ No valid keywords aggregated.")
        return df

    df = df.sort_values(by="effectiveness_score", ascending=False)
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    df.to_csv(output_file, index=False)
    print(f"[aggregator] ✅ Saved keyword insights CSV → {output_file}")
    return df


if __name__ == "__main__":
    aggregate_keyword_insights()
