import pandas as pd
import glob, os

def integrate_with_helium10(enriched_path="results/enriched_keywords.csv", helium_folder="data/helium10_exports"):
    """
    Combine enriched keywords with Helium10 export data (if provided).
    """
    # Load enriched keywords
    if not os.path.exists(enriched_path):
        print("[keyword_aggregator_ext] ⚠️ No enriched keywords found.")
        enriched_df = pd.DataFrame(columns=["suggested_keyword"])
    else:
        enriched_df = pd.read_csv(enriched_path)

    # Load Helium10 CSVs (Magnet exports)
    files = glob.glob(os.path.join(helium_folder, "*.csv"))
    h10_rows = []
    for f in files:
        try:
            df = pd.read_csv(f)
            df.columns = [c.lower().replace(" ", "_") for c in df.columns]
            if "keyword" in df.columns:
                h10_rows.append(df[["keyword", "search_volume"]])
        except Exception as e:
            print(f"[keyword_aggregator_ext] Failed reading {f}: {e}")

    if h10_rows:
        h10_df = pd.concat(h10_rows, ignore_index=True)
    else:
        h10_df = pd.DataFrame(columns=["keyword", "search_volume"])

    # Merge
    enriched_df = enriched_df.rename(columns={"suggested_keyword": "keyword"})
    combined = pd.concat([enriched_df, h10_df], ignore_index=True)
    combined = combined.drop_duplicates(subset=["keyword"]).fillna({"search_volume": 0})
    combined["source"] = combined["search_volume"].apply(lambda x: "helium10" if x > 0 else "keywordtool")
    combined = combined.sort_values(by="search_volume", ascending=False)

    output_path = "results/final_enriched_keywords.csv"
    combined.to_csv(output_path, index=False)
    print(f"[keyword_aggregator_ext] ✅ Saved merged enriched dataset → {output_path}")
    return combined
