import yaml
import requests
from bs4 import BeautifulSoup
import json
import re
import csv
import smtplib
from email.message import EmailMessage

# -------------------------
# Load YAML files
# -------------------------
with open("track_akarsh.yaml", "r", encoding="utf-8") as f:
    data = yaml.safe_load(f)

keywords_asins = data.get("tracking", {}).get("keywords_asins", {})

with open("email.yaml", "r", encoding="utf-8") as f:
    email_config = yaml.safe_load(f).get("email", {})

# -------------------------
# Prepare ASIN list (unique)
# -------------------------
all_asins = set()
for asin_list in keywords_asins.values():
    all_asins.update(asin_list)

# -------------------------
# Utility functions
# -------------------------
def clean_text(text):
    if not text:
        return []
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = text.split()
    tokens = [t for t in tokens if len(t) > 2]
    return tokens

def fetch_backend_keywords(asin):
    url = f"https://www.amazon.in/dp/{asin}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code != 200:
            print(f"Error fetching {asin}: status {r.status_code}")
            return []
    except Exception as e:
        print(f"Exception fetching {asin}: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    keywords = []

    # Title
    title_tag = soup.select_one("#productTitle")
    if title_tag:
        keywords += clean_text(title_tag.get_text())

    # Bullets
    bullets = soup.select("#feature-bullets li span")
    for b in bullets:
        keywords += clean_text(b.get_text())

    # Product description
    desc_tag = soup.select_one("#productDescription")
    if desc_tag:
        keywords += clean_text(desc_tag.get_text())

    # JSON-LD
    json_ld_tags = soup.find_all("script", type="application/ld+json")
    for tag in json_ld_tags:
        try:
            jd = json.loads(tag.string)
            if isinstance(jd, dict) and jd.get("@type") == "Product":
                keywords += clean_text(jd.get("name", ""))
                keywords += clean_text(jd.get("description", ""))
            elif isinstance(jd, list):
                for entry in jd:
                    if entry.get("@type") == "Product":
                        keywords += clean_text(entry.get("name", ""))
                        keywords += clean_text(entry.get("description", ""))
        except Exception:
            continue

    return list(set(keywords))

# -------------------------
# Extract keywords
# -------------------------
asin_keywords = {}
for asin in all_asins:
    print(f"Processing ASIN: {asin}")
    kw = fetch_backend_keywords(asin)
    asin_keywords[asin] = kw
    print(f"Found {len(kw)} keywords")

# -------------------------
# Save CSV
# -------------------------
csv_file = "asin_backend_keywords.csv"
with open(csv_file, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["ASIN", "Keywords"])
    for asin, kw_list in asin_keywords.items():
        writer.writerow([asin, ", ".join(kw_list)])

print(f"CSV saved: {csv_file}")

# -------------------------
# Send email
# -------------------------
msg = EmailMessage()
msg["Subject"] = "ASIN Backend Keywords"
msg["From"] = email_config.get("from")
msg["To"] = ", ".join(email_config.get("to", []))
msg.set_content("Please find attached the ASIN backend keywords CSV file.")

# Attach CSV
with open(csv_file, "rb") as f:
    msg.add_attachment(f.read(), maintype="text", subtype="csv", filename=csv_file)

try:
    with smtplib.SMTP(email_config.get("smtp_server"), email_config.get("smtp_port")) as server:
        server.starttls()
        server.login(email_config.get("from"), email_config.get("password"))
        server.send_message(msg)
        print("Email sent successfully!")
except Exception as e:
    print(f"Error sending email: {e}")
