"""
Simple Vercel-compatible Flask app
"""
from flask import Flask, jsonify
import urllib.request
import json
import time

app = Flask(__name__)

@app.route('/')
def index():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Telegram Trading Bot</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body { font-family: Arial, sans-serif; background: #1e3c72; color: white; padding: 20px; }
            .container { max-width: 400px; margin: 0 auto; }
            .header { text-align: center; margin-bottom: 30px; }
            .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
            .stat { background: rgba(255,255,255,0.1); padding: 15px; border-radius: 10px; text-align: center; }
            .positive { color: #4CAF50; }
            .negative { color: #f44336; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🤖 Trading Bot</h1>
                <p>Live Market Data</p>
            </div>
            <div class="stats" id="stats">
                <div class="stat">Loading...</div>
            </div>
        </div>
        <script>
            async function loadData() {
                try {
                    const response = await fetch('/api/market');
                    const data = await response.json();
                    const changeClass = data.change >= 0 ? 'positive' : 'negative';
                    const demoLabel = data.demo ? ' (Demo)' : '';
                    document.getElementById('stats').innerHTML = `
                        <div class="stat">
                            <div>BTC Price${demoLabel}</div>
                            <div>$${data.price.toLocaleString()}</div>
                        </div>
                        <div class="stat">
                            <div>24h Change</div>
                            <div class="${changeClass}">${data.changePercent.toFixed(2)}%</div>
                        </div>
                        <div class="stat">
                            <div>Status</div>
                            <div>${data.demo ? 'Demo Mode' : 'Live Data'}</div>
                        </div>
                        <div class="stat">
                            <div>Updated</div>
                            <div>${new Date().toLocaleTimeString()}</div>
                        </div>
                    `;
                } catch (error) {
                    console.error('Error loading market data:', error);
                    document.getElementById('stats').innerHTML = `
                        <div class="stat">
                            <div>BTC Price</div>
                            <div style="color: #f44336;">Loading Failed</div>
                        </div>
                        <div class="stat">
                            <div>Status</div>
                            <div style="color: #f44336;">API Error</div>
                        </div>
                        <div class="stat">
                            <div>Last Try</div>
                            <div>${new Date().toLocaleTimeString()}</div>
                        </div>
                        <div class="stat">
                            <div>Action</div>
                            <div>Retrying...</div>
                        </div>
                    `;
                }
            }
            loadData();
            setInterval(loadData, 30000);
        </script>
    </body>
    </html>
    """

@app.route('/api/market')
def market():
    try:
        # Try alternative crypto API that works with Vercel
        import urllib.request
        
        # Try CoinGecko API (no API key required)
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true"
        
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (compatible; TradingBot/1.0)')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            
        btc_data = data['bitcoin']
        price = btc_data['usd']
        change_percent = btc_data.get('usd_24h_change', 0)
        change = (change_percent / 100) * price
        
        return jsonify({
            'price': round(price, 2),
            'change': round(change, 2),
            'changePercent': round(change_percent, 2),
            'timestamp': int(time.time() * 1000),
            'source': 'coingecko'
        })
        
    except Exception as e:
        # If that fails, try another approach
        try:
            # Try Binance public API with different approach
            url = "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT"
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            req.add_header('Accept', 'application/json')
            
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
            
            return jsonify({
                'price': float(data['lastPrice']),
                'change': float(data['priceChange']),
                'changePercent': float(data['priceChangePercent']),
                'timestamp': int(time.time() * 1000),
                'source': 'binance'
            })
            
        except Exception as e2:
            # Return error message instead of fallback data
            return jsonify({
                'error': f'Unable to fetch live data: {str(e)}, {str(e2)}',
                'timestamp': int(time.time() * 1000)
            }), 503

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

# For Vercel
application = app

if __name__ == '__main__':
    app.run(debug=True)