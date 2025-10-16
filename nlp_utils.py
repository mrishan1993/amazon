# nlp_utils.py
import re
from typing import List, Tuple, Dict
import spacy
from sklearn.feature_extraction.text import TfidfVectorizer
from collections import Counter
from sklearn.cluster import KMeans

nlp = spacy.load("en_core_web_sm", disable=["ner"])

STOP_KEYWORDS = set([
    "amazon","product","option","total","order","date","payment","shipping","cost","item","use","uses"
])

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.lower().strip()

def lemmatize_text(text: str) -> str:
    doc = nlp(text)
    lemmas = [token.lemma_ for token in doc if not token.is_stop and token.is_alpha]
    return " ".join(lemmas)

def extract_candidate_keywords(text: str, top_k: int = 50):
    text = clean_text(text)
    if not text:
        return []

    doc = nlp(text)
    candidate_phrases = []

    # Noun phrases
    for chunk in doc.noun_chunks:
        phrase = chunk.text.lower().strip()
        phrase = re.sub(r'\b(the|a|an|this|that|these|those|you|your|it|its|they|their|i|we|our)\b', '', phrase)
        phrase = re.sub(r'\s+', ' ', phrase).strip()
        if len(phrase.split()) < 2 or phrase in STOP_KEYWORDS:
            continue
        candidate_phrases.append(phrase)

    # Strong nouns/adjectives
    for token in doc:
        if token.pos_ in {"NOUN","PROPN","ADJ"} and not token.is_stop and len(token.text) > 2 and token.text.lower() not in STOP_KEYWORDS:
            candidate_phrases.append(token.lemma_.lower())

    # Frequency + TF-IDF
    from collections import Counter
    freq = Counter(candidate_phrases)
    if not freq:
        return []

    from sklearn.feature_extraction.text import TfidfVectorizer
    vect = TfidfVectorizer(ngram_range=(1,2), stop_words="english", max_features=2000)
    tfidf = vect.fit_transform([" ".join(candidate_phrases)])
    scores = dict(zip(vect.get_feature_names_out(), tfidf.toarray().flatten()))

    combined = {k: scores.get(k,0) + (freq[k]/max(freq.values())) for k in freq}
    ranked = sorted(combined.items(), key=lambda x: x[1], reverse=True)[:top_k]
    return [(k,v) for k,v in ranked if k not in STOP_KEYWORDS]

def semantic_cluster(phrases: List[str], n_clusters: int = 6) -> Dict[int, List[str]]:
    """
    Cluster keyword phrases into semantic groups using TF-IDF + KMeans.
    Returns cluster_id -> list(phrases)
    """
    if not phrases:
        return {}
    vect = TfidfVectorizer(ngram_range=(1,2), stop_words="english")
    X = vect.fit_transform(phrases)
    n_clusters = min(n_clusters, max(1, len(phrases)//3))
    model = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = model.fit_predict(X)
    clusters = {}
    for lbl, phrase in zip(labels, phrases):
        clusters.setdefault(lbl, []).append(phrase)
    return clusters
