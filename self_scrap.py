import requests
from bs4 import BeautifulSoup
import yaml
import time
import csv
import smtplib
import ssl
from email.message import EmailMessage
import os
from datetime import datetime

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/105.0.0.0 Safari/537.36"
    )
}

# ---------- Function: Get BSR ----------
def get_bsr(domain, asin):
    url = f"{domain}/dp/{asin}"
    r = requests.get(url, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")

    bsr_text = None
    for li in soup.select("#detailBullets_feature_div li"):
        if "Best Sellers Rank" in li.get_text():
            bsr_text = li.get_text(strip=True)
            break
    if not bsr_text:
        for tr in soup.select("#productDetails_detailBullets_sections1 tr"):
            if "Best Sellers Rank" in tr.get_text():
                bsr_text = tr.get_text(strip=True)
                break
    return bsr_text if bsr_text else "BSR not available"

# ---------- Function: Get Keyword Rank ----------
def get_keyword_rank(domain, asin, keyword, max_pages=5):
    for page in range(1, max_pages + 1):
        url = f"{domain}/s?k={keyword}&page={page}"
        r = requests.get(url, headers=HEADERS)
        soup = BeautifulSoup(r.text, "html.parser")

        results = soup.find_all("div", {"data-asin": True})
        for idx, item in enumerate(results, start=1):
            if item["data-asin"] == asin:
                return page, idx  # return separately
        time.sleep(1)
    return None, None   # not found

# ---------- Function: Save CSV ----------
def save_to_csv(data, filename="results.csv"):
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Keyword", "ASIN", "Page", "Position", "BSR", "Timestamp"])
        for row in data:
            writer.writerow(row)

# ---------- Function: Email Results ----------
def send_email(email_cfg, subject, body, attachment="results.csv"):
    msg = EmailMessage()
    msg["From"] = email_cfg["from"]
    msg["To"] = email_cfg["to"]
    msg["Subject"] = subject
    msg.set_content(body)

    with open(attachment, "rb") as f:
        file_data = f.read()
        file_name = f.name
    msg.add_attachment(file_data, maintype="text", subtype="csv", filename=file_name)

    context = ssl.create_default_context()
    with smtplib.SMTP(email_cfg["smtp_server"], email_cfg["smtp_port"]) as server:
        server.starttls(context=context)
        server.login(email_cfg["from"], email_cfg["password"])
        server.send_message(msg)

# ---------- Main ----------
if __name__ == "__main__":
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    yaml_path = os.path.join(BASE_DIR, "keywords_car_body_polish.yaml")

    with open(yaml_path, "r") as f:
        cfg = yaml.safe_load(f)

    asin = cfg["asin"]
    domain = cfg["domain"]
    keywords = cfg["keywords"]

    email_path = os.path.join(BASE_DIR, "email.yaml")
    with open(email_path, "r") as f:
        email_config = yaml.safe_load(f)
    email_cfg = email_config["email"]

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    results = []

    for keyword in keywords:
        page, pos = get_keyword_rank(domain, asin, keyword)
        bsr = get_bsr(domain, asin)

        page_val = page if page else "Not found"
        pos_val = pos if pos else "Not found"

        print(f"{keyword} → {asin} → Page {page_val}, Position {pos_val} | BSR: {bsr}")
        results.append([keyword, asin, page_val, pos_val, bsr, timestamp])

    # Save CSV
    save_to_csv(results)

    # Email
    send_email(
        email_cfg,
        subject="Amazon Keyword Rank Report",
        body=f"Here is the keyword rank report ({timestamp}).",
        attachment="results.csv"
    )

    print("\n✅ Results saved to results.csv and emailed.")
