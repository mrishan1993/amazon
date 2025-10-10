# sentiment_utils.py
from nltk.sentiment.vader import SentimentIntensityAnalyzer
from collections import Counter
import spacy
from typing import List, Dict

sid = SentimentIntensityAnalyzer()
nlp = spacy.load("en_core_web_sm", disable=["ner"])

def review_sentiment_score(review: str) -> float:
    scores = sid.polarity_scores(review)
    return scores['compound']

def aggregate_review_sentiment(reviews: List[str]) -> Dict:
    if not reviews:
        return {"avg_compound": 0.0, "count": 0}
    scores = [review_sentiment_score(r) for r in reviews]
    return {"avg_compound": sum(scores)/len(scores), "count": len(scores)}

def extract_mentioned_features(reviews: List[str], top_n: int = 15) -> Dict[str, Dict]:
    """
    Extract noun phrases and associate average sentiment
    """
    feature_counter = Counter()
    feature_sentiments = {}
    for r in reviews:
        doc = nlp(r)
        # simple noun-chunk extraction
        for chunk in doc.noun_chunks:
            phrase = chunk.lemma_.lower().strip()
            if len(phrase) < 3:
                continue
            feature_counter[phrase] += 1
            feature_sentiments.setdefault(phrase, []).append(review_sentiment_score(r))
    # build top features with avg sentiment
    top = feature_counter.most_common(top_n)
    result = {}
    for feat, cnt in top:
        avg_sent = sum(feature_sentiments.get(feat, [0])) / len(feature_sentiments.get(feat, [1]))
        result[feat] = {"count": cnt, "avg_sentiment": avg_sent}
    return result
