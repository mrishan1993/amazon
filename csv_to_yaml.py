import csv
import yaml

CSV_FILE = "asins_keywords.csv"
YAML_FILE = "asins_keywords.yaml"
DOMAIN = "https://www.amazon.in"

def detect_delimiter(sample_line):
    """Try to detect whether CSV is comma, semicolon, or tab separated"""
    if '\t' in sample_line:
        return '\t'
        return ','
    elif ';' in sample_line:
        return ';'
    else:
        return ','  # default fallback

def csv_to_yaml(csv_file, yaml_file):
    data = []

    # Open the CSV
    with open(csv_file, "r", encoding="utf-8") as f:
        first_line = f.readline()
        delimiter = detect_delimiter(first_line)
        f.seek(0)

        reader = csv.reader(f, delimiter=delimiter)
        for i, row in enumerate(reader, start=1):
            if not row or len(row) < 2:
                print(f"⚠️ Skipping line {i}: {row}")
                continue

            asin = row[0].strip()
            keywords_raw = row[1].strip()

            # Split keywords by ';' (since they are in one cell)
            keywords = [kw.strip() for kw in keywords_raw.split(';') if kw.strip()]

            if not asin or not keywords:
                print(f"⚠️ Skipping line {i}: Missing data → {row}")
                continue

            data.append({
                "asin": asin,
                "domain": DOMAIN,
                "keywords": keywords
            })

    if not data:
        print("❌ No valid data found. Check your CSV formatting.")
    else:
        # Write YAML
        with open(yaml_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)
        print(f"✅ YAML file created successfully: {yaml_file}")

if __name__ == "__main__":
    csv_to_yaml(CSV_FILE, YAML_FILE)
