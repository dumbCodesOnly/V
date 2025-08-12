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
                    console.error('Error:', error);
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
    # Use realistic demo data for Vercel deployment
    # External API calls are restricted in serverless environment
    import random
    
    # Generate realistic BTC price movements
    base_price = 120000 + random.uniform(-2000, 2000)
    change = random.uniform(-1000, 2000)
    change_percent = (change / base_price) * 100
    
    return jsonify({
        'price': round(base_price, 2),
        'change': round(change, 2),
        'changePercent': round(change_percent, 2),
        'timestamp': int(time.time() * 1000),
        'demo': True
    })

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

# For Vercel
application = app

if __name__ == '__main__':
    app.run(debug=True)