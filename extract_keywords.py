#!/usr/bin/env python3
"""improved_asin_keywords.py
Fetch PDPs for ASINs listed in track.yaml and extract cleaned, high-quality keyword phrases.
"""

import os, re, time, random, csv, yaml, logging
from bs4 import BeautifulSoup
import requests
from datetime import datetime, timezone

# -------------------------
# Config
# -------------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DEFAULT_DOMAIN = "https://www.amazon.in"
TRACK_YAML = os.path.join(BASE_DIR, "track.yaml")
OUT_CSV = os.path.join(BASE_DIR, f"asin_keywords_cleaned_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv")

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

MAX_KWS_PER_ASIN = 40

# -------------------------
# Logging
# -------------------------
def init_logging():
    log = logging.getLogger("asin-keywords-clean")
    log.setLevel(logging.INFO)
    if log.handlers:
        log.handlers.clear()
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(ch)
    return log

logger = init_logging()

# -------------------------
# Helpers
# -------------------------
STOPWORDS = {
    "the","and","a","an","of","for","with","to","in","on","by","from","this","that","it","its",
    "item","items","number","net","quantity","pack","packof","pack-of","see","more","click","here",
    "amazon","amazonin","amazon","seller","manufacturer","brand","brandname"
}

NOISE_TOKENS = {
    "upc","ean","gtin","mpn","isbn","sku","weight","gram","grams","ml","ltr","litre","millilitre",
    "milliliter","cm","mm","inch","inches","dimensions","size","length","width","height"
}

# allow numeric tokens that are short (e.g., '3m','5l') but block long numeric sequences (UPCs)
LONG_DIGIT_RE = re.compile(r"^\d{5,}$")
DIMENSION_RE = re.compile(r"^\d+(\.\d+)?x\d+(\.\d+)?(x\d+(\.\d+)?)?$", re.IGNORECASE)  # e.g. 23x15x10

def load_yaml(p):
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def make_session():
    s = requests.Session()
    s.headers.update(BASE_HEADERS)
    return s

# clean token basic normalization
def normalize_token(tok):
    t = tok.strip().lower()
    t = re.sub(r"[\u2018\u2019\u201c\u201d’“”]", "'", t)
    t = re.sub(r"[^a-z0-9\- ]+", " ", t)  # keep alnum, hyphen, spaces
    t = re.sub(r"\s+", " ", t).strip()
    return t

def is_noise_token(tok):
    if not tok: return True
    if tok in STOPWORDS: return True
    if tok in NOISE_TOKENS: return True
    if LONG_DIGIT_RE.match(tok): return True
    if DIMENSION_RE.match(tok): return True
    # tokens like '070382005368' or '00070382...' are filtered above
    if len(tok) <= 1 and not tok.isalpha():  # single letter like 'g' or single-digit number
        return True
    return False

# create 1-3 gram candidate phrases from a source text, applying token-level filtering
def extract_phrases_from_text(text, max_phrases=40):
    if not text:
        return []
    text = text.lower()
    # remove repeated 'amazon' templates
    text = re.sub(r"\bamazon\b|\bin\b\s*car\b|\bin\b\s*india\b", " ", text)
    # token list
    words = re.findall(r"[a-z0-9\-]+", text)
    if not words:
        return []
    candidates = []
    seen = set()
    # prefer 3-grams then 2-grams then 1-grams
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            toks = [normalize_token(w) for w in words[i:i+n]]
            # skip if any token is noise OR phrase is majority-stopwords
            if any(is_noise_token(t) for t in toks):
                continue
            swcount = sum(1 for t in toks if t in STOPWORDS)
            if swcount >= n:  # all or majority stopwords
                continue
            phrase = " ".join(toks)
            # skip if already seen or too short overall
            if phrase in seen: continue
            if len(phrase) < 3: continue
            # heuristic: phrase should contain at least one alpha char
            if not re.search(r"[a-z]", phrase): continue
            seen.add(phrase)
            candidates.append(phrase)
            if len(candidates) >= max_phrases:
                return candidates
    return candidates

# main extraction function operating on parsed soup
def fetch_clean_keywords_from_html(html):
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out = []
    # 1) title (highest priority)
    title_el = soup.select_one("#productTitle") or soup.select_one("meta[property='og:title']")
    if title_el:
        ttxt = title_el.get_text(" ", strip=True) if hasattr(title_el, "get_text") else title_el.get("content", "")
        out += extract_phrases_from_text(ttxt, max_phrases=15)

    # 2) JSON-LD keywords (if present)
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = s.string and s.string.strip()
            if not data: continue
            obj = None
            try:
                obj = __import__("json").loads(data)
            except Exception:
                continue
            if isinstance(obj, dict):
                if "keywords" in obj and obj["keywords"]:
                    if isinstance(obj["keywords"], list):
                        txt = " ".join(obj["keywords"])
                    else:
                        txt = str(obj["keywords"])
                    out += extract_phrases_from_text(txt, max_phrases=10)
                # sometimes description present
                if "description" in obj and obj["description"]:
                    out += extract_phrases_from_text(str(obj["description"]), max_phrases=10)
        except Exception:
            continue

    # 3) feature bullets
    bullets = []
    for el in soup.select("#feature-bullets li, #feature-bullets--feature-bullets li, #feature-bullets div.a-list-item"):
        txt = " ".join(el.stripped_strings)
        if txt:
            bullets.append(txt)
    if bullets:
        out += extract_phrases_from_text(" ".join(bullets), max_phrases=30)

    # 4) product description / a+ content (but avoid product details)
    desc_texts = []
    for sel in ("#productDescription", "#productDescription_feature_div", "#aplus", ".a-section.a-spacing-small#productDescription"):
        el = soup.select_one(sel)
        if el:
            desc_texts.append(" ".join(el.stripped_strings))
    # also capture A+ module text
    for el in soup.select(".aplus, .a-plus, #aplus, div[data-asin] .a-section"):
        # small filter to avoid huge irrelevant blocks; only take if it has text and not table-like
        txt = " ".join(el.stripped_strings)
        if txt and len(txt) < 2000:
            desc_texts.append(txt)
    if desc_texts:
        out += extract_phrases_from_text(" ".join(desc_texts), max_phrases=40)

    # final cleaning & ranking: preserve order + dedupe
    final = []
    seen = set()
    for p in out:
        # final filters (avoid "pack of 1", "see more" remnants)
        if any(tok in p for tok in ("pack of", "packof", "see more", "item weight", "number of")):
            continue
        if "amazon" in p:
            continue
        if len(p) < 3:
            continue
        if p in seen:
            continue
        seen.add(p)
        final.append(p)
        if len(final) >= MAX_KWS_PER_ASIN:
            break
    return final

# -------------------------
# Runner
# -------------------------
def main():
    cfg = load_yaml(TRACK_YAML)
    tracking = cfg.get("tracking", {})
    asin_map = tracking.get("keywords_asins", {})
    sess = make_session()

    unique_asins = {a for lst in asin_map.values() for a in lst}
    rows = []
    for asin in unique_asins:
        url = f"{DEFAULT_DOMAIN}/dp/{asin}"
        logger.info(f"Fetching {asin} -> {url}")
        try:
            r = sess.get(url, timeout=30)
            if r.status_code != 200:
                logger.warning(f"status {r.status_code} for {asin}")
                html = r.text
            else:
                html = r.text
        except Exception as e:
            logger.error(f"request failed for {asin}: {e}")
            html = None

        kws = fetch_clean_keywords_from_html(html)
        rows.append({"asin": asin, "keywords": ";".join(kws)})
        logger.info(f"{asin} -> extracted {len(kws)} keywords")

        time.sleep(random.uniform(0.8, 1.6))

    # write csv
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["asin", "keywords"])
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    logger.info(f"Wrote cleaned keywords CSV -> {OUT_CSV}")

if __name__ == "__main__":
    main()
