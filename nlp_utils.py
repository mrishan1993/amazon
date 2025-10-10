# nlp_utils.py
import re
from typing import List, Tuple, Dict
import spacy
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans

nlp = spacy.load("en_core_web_sm", disable=["parser", "ner"])

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

def extract_candidate_keywords(text: str, top_k: int = 50) -> List[Tuple[str, float]]:
    """
    Use TF-IDF to pull top candidate keywords (unigrams + bigrams).
    Returns list of (phrase, score).
    """
    text = clean_text(text)
    if not text:
        return []
    # vectorize
    vect = TfidfVectorizer(ngram_range=(1,2), max_features=1000, stop_words="english")
    tfidf = vect.fit_transform([text])
    scores = dict(zip(vect.get_feature_names_out(), tfidf.toarray().flatten()))
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    return ranked

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
