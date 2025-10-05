#!/usr/bin/env python3
import os
import yaml
import requests
import random
import time
import re
import csv
import smtplib
from email.message import EmailMessage
from bs4 import BeautifulSoup
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
yaml_path = os.path.join(BASE_DIR, "track_akarsh.yaml")
email_path = os.path.join(BASE_DIR, "email_akarsh.yaml")

# Load YAMLs
with open(yaml_path, "r", encoding="utf-8") as f:
    data = yaml.safe_load(f)
keywords_asins = data.get("tracking", {}).get("keywords_asins", {})

with open(email_path, "r", encoding="utf-8") as f:
    email_config = yaml.safe_load(f).get("email", {})

# Prepare ASIN list (unique)
all_asins = set()
for asin_list in keywords_asins.values():
    all_asins.update(asin_list)

# Human-like headers pool
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:116.0) Gecko/20100101 Firefox/116.0",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
]
ACCEPT_LANGS = ["en-IN,en;q=0.9", "en-GB,en;q=0.9,en-US;q=0.8", "en-US,en;q=0.9"]

# Utility functions
def clean_text(text):
    if not text:
        return []
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = text.split()
    return [t for t in tokens if len(t) > 2]

def random_headers(asin):
    ua = random.choice(USER_AGENTS)
    accept_lang = random.choice(ACCEPT_LANGS)
    referer_kw = random.choice(list(keywords_asins.keys()))
    referer = f"https://www.amazon.in/s?k={referer_kw.replace(' ', '+')}"
    headers = {
        "User-Agent": ua,
        "Accept-Language": accept_lang,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": referer,
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Sec-CH-UA": '"Chromium";v="120", "Not:A-Brand";v="99"',
        "Sec-CH-UA-Mobile": "?0" if "Mobile" not in ua else "?1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
    }
    return headers

def fetch_backend_keywords(asin):
    url = f"https://www.amazon.in/dp/{asin}"
    try:
        r = requests.get(url, headers=random_headers(asin), timeout=30)
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

    # Description
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

# Extract keywords
asin_keywords = {}
for asin in all_asins:
    print(f"Processing ASIN: {asin}")
    kw = fetch_backend_keywords(asin)
    asin_keywords[asin] = kw
    print(f"Found {len(kw)} keywords")
    # Random delay to mimic human
    time.sleep(random.uniform(2, 5))

# Save CSV
csv_file = "asin_backend_keywords.csv"
with open(csv_file, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["ASIN", "Keywords"])
    for asin, kw_list in asin_keywords.items():
        writer.writerow([asin, ", ".join(kw_list)])
print(f"CSV saved: {csv_file}")

# Send email
msg = EmailMessage()
msg["Subject"] = "ASIN Backend Keywords"
msg["From"] = email_config.get("from")
msg["To"] = ", ".join(email_config.get("to", []))
msg.set_content("Please find attached the ASIN backend keywords CSV file.")
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
