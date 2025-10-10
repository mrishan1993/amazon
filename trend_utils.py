from pytrends.request import TrendReq
import pandas as pd

pytrends = TrendReq(hl='en-US', tz=330)

def get_trend_score(keyword, timeframe='today 3-m'):
    """
    Returns a normalized trend score (0–100) for the last 3 months.
    """
    try:
        print("[trend_utils] Fetching trend score for", keyword)
        pytrends.build_payload([keyword], timeframe=timeframe)
        df = pytrends.interest_over_time()
        if df.empty:
            return 0
        trend_avg = df[keyword].mean()
        trend_last = df[keyword].iloc[-1]
        growth = ((trend_last - trend_avg) / (trend_avg + 1e-5)) * 100
        return round(growth, 2)
    except Exception as e:
        print(f"[trend_utils] Failed for {keyword}: {e}")
        return 0
