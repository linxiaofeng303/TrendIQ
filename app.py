from flask import Flask, render_template, request, jsonify
import joblib
import numpy as np
import pandas as pd
import yfinance as yf
import os
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

# Conditionally import TensorFlow (to allow deployment on Vercel where TF is omitted)
try:
    from tensorflow.keras.models import load_model
    HAS_TENSORFLOW = True
except ImportError:
    HAS_TENSORFLOW = False

app = Flask(__name__, template_folder="frontend", static_folder="frontend")

def mt5_bridge_configured():
    return bool(os.getenv("MT5_BRIDGE_URL", "").strip())

def mt5_bridge_request(endpoint, params=None):
    """Call the separately hosted MT5 bridge without exposing broker credentials."""
    base_url = os.getenv("MT5_BRIDGE_URL", "").strip().rstrip("/")
    token = os.getenv("MT5_BRIDGE_TOKEN", "").strip()
    if not base_url:
        raise RuntimeError("MT5 data bridge is not configured.")
    if not token:
        raise RuntimeError("MT5_BRIDGE_TOKEN is missing from the website environment.")

    query = urllib.parse.urlencode(params or {})
    url = f"{base_url}/api/{endpoint}"
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"MT5 bridge returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not reach the MT5 data bridge: {exc}") from exc

    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload

def fetch_mt5_gold_history(ticker_symbol):
    """Fetch daily broker candles for the website's XAUUSD alias."""
    if ticker_symbol.upper() not in {"XAUUSD", "GOLD"} or not mt5_bridge_configured():
        return None

    symbol = os.getenv("MT5_GOLD_SYMBOL", "GOLD").strip() or "GOLD"
    payload = mt5_bridge_request(
        "history", {"symbol": symbol, "timeframe": "D1", "count": 1000}
    )
    rates = payload.get("rates", []) if isinstance(payload, dict) else []
    if not rates:
        raise RuntimeError(f"MT5 returned no daily history for {symbol}.")

    hist_data = pd.DataFrame(rates)
    if "time" not in hist_data or "close" not in hist_data:
        raise RuntimeError("MT5 bridge returned rates in an unexpected format.")
    hist_data.index = pd.to_datetime(hist_data.pop("time"), unit="s", utc=True)
    hist_data["Close"] = pd.to_numeric(hist_data["close"], errors="coerce")
    return hist_data

def fetch_mt5_gold_quote():
    if not mt5_bridge_configured():
        return None
    symbol = os.getenv("MT5_GOLD_SYMBOL", "GOLD").strip() or "GOLD"
    payload = mt5_bridge_request("quote", {"symbol": symbol})
    price = payload.get("price") if isinstance(payload, dict) else None
    if price is None:
        raise RuntimeError(f"MT5 bridge returned no current quote for {symbol}.")
    return {
        "symbol": "XAUUSD",
        "price": round(float(price), 2),
        "change": round(float(payload.get("change", 0)), 2),
        "changePercent": round(float(payload.get("changePercent", 0)), 2),
    }

# ==============================================================================
# Financial Sentiment Lexicon & Analyzer
# ==============================================================================
POSITIVE_FINANCIAL_WORDS = {
    'surge', 'surges', 'surged', 'jump', 'jumps', 'jumped', 'gain', 'gains', 'gained',
    'profit', 'profits', 'profitable', 'beat', 'beats', 'beating', 'rally', 'rallies',
    'bull', 'bullish', 'growth', 'grow', 'growing', 'record', 'high', 'higher', 'outperform',
    'upgrade', 'upgraded', 'boost', 'boosts', 'soar', 'soars', 'soaring', 'strong', 'strength',
    'revenue', 'dividend', 'expansion', 'buy', 'optimism', 'positive', 'success', 'breakthrough'
}

NEGATIVE_FINANCIAL_WORDS = {
    'drop', 'drops', 'dropped', 'fall', 'falls', 'falling', 'loss', 'losses', 'miss',
    'misses', 'missed', 'plunge', 'plunges', 'plunging', 'decline', 'declines', 'declining',
    'bear', 'bearish', 'warning', 'warns', 'warned', 'risk', 'risks', 'risky', 'downgrade',
    'downgraded', 'cut', 'cuts', 'slump', 'slumps', 'slumping', 'weak', 'weakness',
    'debt', 'inflation', 'lawsuit', 'sell', 'pessimism', 'negative', 'failure', 'tumble', 'crash'
}

def analyze_sentiment(text):
    if not text:
        return 0.0
    words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    if not words:
        return 0.0
    pos_count = sum(1 for w in words if w in POSITIVE_FINANCIAL_WORDS)
    neg_count = sum(1 for w in words if w in NEGATIVE_FINANCIAL_WORDS)
    total = pos_count + neg_count
    if total == 0:
        return 0.0
    return (pos_count - neg_count) / total

def get_news_and_sentiment(ticker_symbol):
    """
    Fetches real news headlines from Yahoo Finance and computes sentiment polarity.
    """
    try:
        ticker = yf.Ticker(ticker_symbol)
        raw_news = ticker.news or []
        
        articles = []
        sentiment_scores = []
        
        for item in raw_news[:5]:
            title = item.get('title', '')
            publisher = item.get('publisher', 'Market News')
            link = item.get('link', '#')
            pub_time = item.get('providerPublishTime')
            date_str = datetime.fromtimestamp(pub_time).strftime('%b %d, %H:%M') if pub_time else "Recent"
            
            score = analyze_sentiment(title)
            sentiment_scores.append(score)
            
            articles.append({
                "title": title,
                "publisher": publisher,
                "link": link,
                "date": date_str,
                "score": round(score, 2)
            })
            
        if sentiment_scores:
            avg_score = float(np.mean(sentiment_scores))
        else:
            avg_score = 0.0
            
        if avg_score > 0.15:
            sentiment_label = "Bullish"
            sentiment_color = "#06ffa5"
        elif avg_score < -0.15:
            sentiment_label = "Bearish"
            sentiment_color = "#ff006e"
        else:
            sentiment_label = "Neutral"
            sentiment_color = "#ffbe0b"
            
        return {
            "score": round(avg_score, 2),
            "label": sentiment_label,
            "color": sentiment_color,
            "article_count": len(articles),
            "articles": articles
        }
    except Exception as e:
        print(f"Error fetching news for {ticker_symbol}: {e}")
        return {
            "score": 0.0,
            "label": "Neutral",
            "color": "#ffbe0b",
            "article_count": 0,
            "articles": []
        }

# ==============================================================================
# Page Routes
# ==============================================================================
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/predict-page")
def predict_page():
    return render_template("predict.html")

@app.route("/about")
@app.route("/methodology")
def about():
    return render_template("about.html")

@app.route("/documentation")
@app.route("/docs")
def documentation():
    return render_template("docs.html")

@app.route("/api-docs")
def api_docs():
    return render_template("api_docs.html")

@app.route("/legal")
@app.route("/privacy")
@app.route("/terms")
@app.route("/disclaimer")
def legal():
    return render_template("legal.html")

@app.route("/api/benchmarks", methods=["GET"])
def get_benchmarks():
    """Returns empirical backtest benchmarks from data/benchmark_results.json"""
    try:
        bench_path = "data/benchmark_results.json"
        if os.path.exists(bench_path):
            with open(bench_path, "r") as f:
                data = json.load(f)
            return jsonify({"status": "success", "benchmarks": data})
        else:
            return jsonify({"status": "error", "message": "Benchmark data not generated yet."}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ==============================================================================
# API Endpoints
# ==============================================================================
@app.route("/live-ticker", methods=["GET"])
def live_ticker():
    """Get live stock data for ticker widget in a single bulk request"""
    tickers = ['AAPL', 'GOOGL', 'MSFT', 'TSLA', 'AMZN', 'NVDA']
    ticker_data = []
    
    try:
        df = yf.download(tickers, period='5d')
        if 'Close' in df:
            close = df['Close'].dropna(how='all')
            for ticker_symbol in tickers:
                if ticker_symbol in close.columns:
                    ticker_close = close[ticker_symbol].dropna()
                    if not ticker_close.empty:
                        current_price = float(ticker_close.iloc[-1])
                        previous_close = float(ticker_close.iloc[-2]) if len(ticker_close) > 1 else current_price
                        
                        change = current_price - previous_close
                        change_percent = (change / previous_close) * 100 if previous_close else 0
                        
                        ticker_data.append({
                            'symbol': ticker_symbol,
                            'price': round(current_price, 2),
                            'change': round(change, 2),
                            'changePercent': round(change_percent, 2)
                        })
                        continue
                
                ticker_data.append({
                    'symbol': ticker_symbol,
                    'price': None,
                    'change': None,
                    'changePercent': None
                })
        else:
            raise ValueError("Close column missing in downloaded data")
    except Exception as e:
        print(f"Error fetching live tickers: {e}")
        for ticker_symbol in tickers:
            ticker_data.append({
                'symbol': ticker_symbol,
                'price': None,
                'change': None,
                'changePercent': None
            })

    if mt5_bridge_configured():
        try:
            ticker_data.append(fetch_mt5_gold_quote())
        except Exception as e:
            print(f"Error fetching XM GOLD quote: {e}")
            ticker_data.append({
                'symbol': 'XAUUSD',
                'price': None,
                'change': None,
                'changePercent': None
            })
            
    return jsonify(ticker_data)

def predict_multi_horizon(ticker):
    """
    Predict 1-day, 7-day, and 30-day horizons with analytical uncertainty bands
    using Ridge Autoregression + Mean Reversion Dynamics.
    """
    from sklearn.linear_model import Ridge
    
    # Gold must come from the user's XM MT5 feed. Never silently fall back to
    # Yahoo Finance for XAUUSD/GOLD, where a different instrument/scale could
    # otherwise produce a plausible-looking but incorrect forecast.
    is_gold = ticker.upper() in {"XAUUSD", "GOLD"}
    if is_gold and not mt5_bridge_configured():
        raise RuntimeError(
            "The XM MT5 gold feed is not configured for this deployment. "
            "Set MT5_BRIDGE_URL, MT5_BRIDGE_TOKEN, and MT5_GOLD_SYMBOL."
        )

    hist_data = fetch_mt5_gold_history(ticker)
    if hist_data is None:
        stock = yf.Ticker(ticker)
        hist_data = stock.history(period="2y")

        if hist_data.empty or len(hist_data) < 40:
            hist_data = stock.history(period="3y")
            if hist_data.empty or len(hist_data) < 40:
                raise ValueError(f"Insufficient historical data available for {ticker}.")
            
    hist_data = hist_data.dropna(subset=['Close'])
    prices = hist_data['Close'].values
    dates = hist_data.index.strftime('%Y-%m-%d').tolist()
    
    current_price = float(prices[-1])
    
    # 2. Build Autoregressive Lag Features (Lags 1-5 + SMA 5, 20)
    window_size = 20
    X, y = [], []
    for i in range(window_size, len(prices)):
        lags = prices[i-5:i][::-1] # 5 most recent lags
        sma_5 = np.mean(prices[i-5:i])
        sma_20 = np.mean(prices[i-20:i])
        feat = list(lags) + [sma_5, sma_20]
        X.append(feat)
        y.append(prices[i])
        
    X = np.array(X)
    y = np.array(y)
    
    # 3. Train Ridge Model
    model = Ridge(alpha=1.0)
    model.fit(X, y)
    
    # Residual Standard Error for Confidence Intervals
    train_preds = model.predict(X)
    residuals = y - train_preds
    sigma_res = float(np.std(residuals))
    
    # 4. Multi-Horizon Autoregressive Rollout (1 to 30 days)
    sim_prices = list(prices)
    forecast_30d = []
    upper_80 = []
    lower_80 = []
    upper_95 = []
    lower_95 = []
    forecast_dates = []
    
    last_date = datetime.strptime(dates[-1], '%Y-%m-%d')
    
    # Generate business days for future dates
    current_f_date = last_date
    for h in range(1, 31):
        current_f_date += timedelta(days=1)
        while current_f_date.weekday() >= 5: # skip weekends
            current_f_date += timedelta(days=1)
        forecast_dates.append(current_f_date.strftime('%Y-%m-%d'))
        
        # Build features from current simulation state
        lags = sim_prices[-5:][::-1]
        sma_5 = np.mean(sim_prices[-5:])
        sma_20 = np.mean(sim_prices[-20:])
        feat = np.array(list(lags) + [sma_5, sma_20]).reshape(1, -1)
        
        next_val = float(model.predict(feat)[0])
        
        # Apply slight dampening for long horizons to reflect mean-reversion
        weight_decay = np.exp(-0.02 * (h - 1))
        next_val = (next_val * weight_decay) + (sma_20 * (1 - weight_decay))
        
        sim_prices.append(next_val)
        forecast_30d.append(round(next_val, 2))
        
        # Uncertainty band expands with sqrt(horizon)
        margin_80 = 1.28 * sigma_res * np.sqrt(h)
        margin_95 = 1.96 * sigma_res * np.sqrt(h)
        
        upper_80.append(round(next_val + margin_80, 2))
        lower_80.append(round(max(0.01, next_val - margin_80), 2))
        upper_95.append(round(next_val + margin_95, 2))
        lower_95.append(round(max(0.01, next_val - margin_95), 2))
        
    # Horizon 1D, 7D, 30D values
    pred_1d = forecast_30d[0]
    pred_7d = forecast_30d[6]
    pred_30d = forecast_30d[29]
    
    change_1d = pred_1d - current_price
    change_pct_1d = (change_1d / current_price) * 100
    trend_1d = "up" if change_1d > 0.05 else "down" if change_1d < -0.05 else "neutral"
    
    change_7d = pred_7d - current_price
    change_pct_7d = (change_7d / current_price) * 100
    
    change_30d = pred_30d - current_price
    change_pct_30d = (change_30d / current_price) * 100
    
    # 5. Volatility & Confidence
    recent_prices = prices[-30:] if len(prices) >= 30 else prices
    volatility = float(np.std(recent_prices))
    avg_price = float(np.mean(recent_prices))
    volatility_ratio = (volatility / avg_price) * 100 if avg_price else 0
    
    # Quantitative Confidence Score (derived from historical residual error)
    mape_proxy = (sigma_res / avg_price) * 100
    confidence = max(40, min(95, int(100 - (mape_proxy * 10))))
    
    # 6. Fetch news sentiment
    sentiment_data = get_news_and_sentiment(ticker)
    
    # 7. Check if benchmark metrics exist in cache
    benchmark_summary = None
    try:
        if os.path.exists("data/benchmark_results.json"):
            with open("data/benchmark_results.json", "r") as f:
                all_b = json.load(f)
                if ticker in all_b:
                    b_item = all_b[ticker]
                    benchmark_summary = {
                        "test_period": b_item.get("test_period"),
                        "model_rmse": b_item.get("ridge_model", {}).get("rmse"),
                        "naive_rmse": b_item.get("naive_baseline", {}).get("rmse"),
                        "model_mape": b_item.get("ridge_model", {}).get("mape"),
                        "directional_accuracy": b_item.get("ridge_model", {}).get("directional_accuracy"),
                        "rmse_improvement_pct": b_item.get("improvement_over_naive_rmse_pct")
                    }
    except Exception as e:
        print(f"Error loading benchmark for {ticker}: {e}")
        
    chart_len = min(30, len(prices))
    chart_data = {
        "dates": dates[-chart_len:],
        "prices": [float(p) for p in prices[-chart_len:]],
        "forecast_dates": forecast_dates,
        "forecast_30d": forecast_30d,
        "upper_80": upper_80,
        "lower_80": lower_80,
        "upper_95": upper_95,
        "lower_95": lower_95
    }
    
    return {
        "ticker": ticker,
        "current_price": round(current_price, 2),
        "predicted_price": round(pred_1d, 2),
        "price_change": round(change_1d, 2),
        "price_change_pct": round(change_pct_1d, 2),
        "trend": trend_1d,
        "horizons": {
            "1d": {
                "price": round(pred_1d, 2),
                "change": round(change_1d, 2),
                "change_pct": round(change_pct_1d, 2),
                "upper_80": upper_80[0],
                "lower_80": lower_80[0],
                "upper_95": upper_95[0],
                "lower_95": lower_95[0]
            },
            "7d": {
                "price": round(pred_7d, 2),
                "change": round(change_7d, 2),
                "change_pct": round(change_pct_7d, 2),
                "upper_80": upper_80[6],
                "lower_80": lower_80[6],
                "upper_95": upper_95[6],
                "lower_95": lower_95[6]
            },
            "30d": {
                "price": round(pred_30d, 2),
                "change": round(change_30d, 2),
                "change_pct": round(change_pct_30d, 2),
                "upper_80": upper_80[29],
                "lower_80": lower_80[29],
                "upper_95": upper_95[29],
                "lower_95": lower_95[29]
            }
        },
        "chart_data": chart_data,
        "confidence": confidence,
        "volatility": round(volatility_ratio, 2),
        "sigma_res": round(sigma_res, 2),
        "sentiment": sentiment_data,
        "benchmark": benchmark_summary,
        "model_type": "regression"
    }

@app.route("/predict", methods=["POST"])
def predict():
    ticker = request.form.get("ticker", "").upper().strip()
    
    if not ticker:
        return jsonify({"error": "Please enter a stock ticker symbol."})
    
    try:
        result = predict_multi_horizon(ticker)
        return jsonify(result)
    except KeyError:
        return jsonify({"error": "Invalid request. Please provide a ticker symbol."})
    except Exception as e:
        return jsonify({"error": f"Prediction error: {str(e)}"})

if __name__ == "__main__":
    app.run(debug=True)
