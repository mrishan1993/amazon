import pandas as pd
import os
import numpy as np

def aggregate_keyword_insights(results_folder="results", output_file="results/combined_keyword_insights.csv"):
    """
    Aggregates all extracted features (keywords) across ASINs into one master CSV.
    Each row = keyword with calculated metrics to help identify top-performing terms.
    """

    # Collect all keyword/feature files
    files = [f for f in os.listdir(results_folder) if f.endswith("_features.csv")]
    keyword_data = {}

    for f in files:
        asin = f.split("_")[0]
        path = os.path.join(results_folder, f)

        try:
            df = pd.read_csv(path)
        except Exception:
            continue

        if df.empty or "feature" not in df.columns:
            continue

        for _, row in df.iterrows():
            kw = str(row["feature"]).strip().lower()
            sentiment = float(row.get("sentiment", 0))
            mentions = int(row.get("mention_count", 1))
            rating = float(row.get("avg_star_rating", 4.0)) if "avg_star_rating" in df.columns else 4.0

            if kw not in keyword_data:
                keyword_data[kw] = {
                    "total_mentions": 0,
                    "asin_count": 0,
                    "total_sentiment": 0,
                    "total_rating": 0,
                    "asin_list": set()
                }

            keyword_data[kw]["total_mentions"] += mentions
            keyword_data[kw]["asin_count"] += 1
            keyword_data[kw]["total_sentiment"] += sentiment
            keyword_data[kw]["total_rating"] += rating
            keyword_data[kw]["asin_list"].add(asin)

    rows = []
    for kw, stats in keyword_data.items():
        asin_count = len(stats["asin_list"])
        avg_sentiment = stats["total_sentiment"] / max(1, asin_count)
        avg_rating = stats["total_rating"] / max(1, asin_count)

        # Effectiveness scoring logic
        effectiveness = (
            (stats["total_mentions"] * 0.4) +
            (asin_count * 1.5) +
            (avg_sentiment * 20) +
            (avg_rating * 10)
        )
        effectiveness = round(np.clip(effectiveness / 10, 0, 10), 2)

        # Suggestion logic
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

    df = pd.DataFrame(rows).sort_values(by="effectiveness_score", ascending=False)
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    df.to_csv(output_file, index=False)
    print(f"[aggregator] ✅ Saved keyword insights CSV → {output_file}")
    return df


if __name__ == "__main__":
    aggregate_keyword_insights()
