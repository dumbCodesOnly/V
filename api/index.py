"""
Vercel serverless function entry point
"""
from flask import Flask, request, jsonify
from datetime import datetime
import urllib.request
import json
import time
import os

# Set Vercel environment variable
os.environ["VERCEL"] = "1"

# Create Flask app directly in serverless function
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")

# Simple storage for demo (in production, use proper database)
bot_messages = []
bot_trades = []
bot_status = {
    'status': 'active',
    'total_messages': 5,
    'total_trades': 2,
    'error_count': 0,
    'last_heartbeat': datetime.utcnow().isoformat()
}

# Demo data initialization
def initialize_demo_data():
    global bot_messages, bot_trades
    
    bot_messages = [
        {
            'id': 1,
            'username': 'demo_user',
            'message': 'Setup API credentials',
            'timestamp': datetime.utcnow().isoformat(),
            'type': 'command'
        },
        {
            'id': 2,
            'username': 'demo_user', 
            'message': 'Create new trade configuration',
            'timestamp': datetime.utcnow().isoformat(),
            'type': 'command'
        }
    ]
    
    bot_trades = [
        {
            'id': 1,
            'user_id': 'demo_user',
            'symbol': 'BTCUSDT',
            'side': 'long',
            'entry_price': 45000,
            'quantity': 0.001,
            'status': 'active',
            'pnl': 250.50,
            'created_at': datetime.utcnow().isoformat()
        },
        {
            'id': 2,
            'user_id': 'demo_user',
            'symbol': 'ETHUSDT', 
            'side': 'short',
            'entry_price': 3200,
            'quantity': 0.01,
            'status': 'closed',
            'pnl': -125.25,
            'created_at': datetime.utcnow().isoformat()
        }
    ]

# Initialize demo data
initialize_demo_data()

# Routes
@app.route('/')
def index():
    """Serve the Telegram Mini-App interface"""
    return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Telegram Trading Bot</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            color: white;
            min-height: 100vh;
            padding: 10px;
        }
        
        .container {
            max-width: 100%;
            margin: 0 auto;
        }
        
        .header {
            text-align: center;
            padding: 20px 0;
            border-bottom: 1px solid rgba(255,255,255,0.1);
            margin-bottom: 20px;
        }
        
        .tabs {
            display: flex;
            justify-content: space-around;
            margin-bottom: 20px;
            background: rgba(255,255,255,0.1);
            border-radius: 25px;
            padding: 5px;
        }
        
        .tab {
            flex: 1;
            padding: 12px;
            text-align: center;
            border-radius: 20px;
            cursor: pointer;
            transition: all 0.3s ease;
            font-weight: 500;
        }
        
        .tab.active {
            background: white;
            color: #1e3c72;
            box-shadow: 0 2px 10px rgba(0,0,0,0.2);
        }
        
        .tab-content {
            display: none;
            background: rgba(255,255,255,0.1);
            border-radius: 15px;
            padding: 20px;
            backdrop-filter: blur(10px);
        }
        
        .tab-content.active {
            display: block;
        }
        
        .market-stats {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 15px;
            margin-bottom: 20px;
        }
        
        .stat-card {
            background: rgba(255,255,255,0.15);
            padding: 15px;
            border-radius: 10px;
            text-align: center;
        }
        
        .stat-value {
            font-size: 1.5em;
            font-weight: bold;
            margin-top: 5px;
        }
        
        .positive { color: #4CAF50; }
        .negative { color: #f44336; }
        
        .chart-container {
            background: rgba(255,255,255,0.95);
            border-radius: 15px;
            padding: 20px;
            margin: 20px 0;
        }
        
        .symbol-selector {
            display: flex;
            gap: 10px;
            margin-bottom: 15px;
            flex-wrap: wrap;
        }
        
        .symbol-btn {
            padding: 8px 15px;
            background: rgba(255,255,255,0.2);
            border: none;
            border-radius: 20px;
            color: white;
            cursor: pointer;
            transition: all 0.3s ease;
        }
        
        .symbol-btn.active {
            background: white;
            color: #1e3c72;
        }
        
        .loading {
            text-align: center;
            padding: 20px;
            opacity: 0.7;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 Telegram Trading Bot</h1>
            <p>Multi-Trade Management System</p>
        </div>
        
        <div class="tabs">
            <div class="tab active" data-tab="market">📈 Market</div>
            <div class="tab" data-tab="trading">⚙️ Trading</div>
            <div class="tab" data-tab="positions">💼 Positions</div>
        </div>
        
        <div id="market" class="tab-content active">
            <div class="symbol-selector">
                <button class="symbol-btn active" data-symbol="BTCUSDT">BTC</button>
                <button class="symbol-btn" data-symbol="ETHUSDT">ETH</button>
                <button class="symbol-btn" data-symbol="BNBUSDT">BNB</button>
                <button class="symbol-btn" data-symbol="ADAUSDT">ADA</button>
                <button class="symbol-btn" data-symbol="DOTUSDT">DOT</button>
                <button class="symbol-btn" data-symbol="SOLUSDT">SOL</button>
            </div>
            
            <div class="market-stats" id="market-stats">
                <div class="loading">Loading market data...</div>
            </div>
            
            <div class="chart-container">
                <canvas id="priceChart"></canvas>
            </div>
        </div>
        
        <div id="trading" class="tab-content">
            <h3>Trading Configuration</h3>
            <p>Configure your trading parameters and manage multiple trade setups.</p>
            <div class="loading">Demo trading interface</div>
        </div>
        
        <div id="positions" class="tab-content">
            <h3>Active Positions</h3>
            <p>Monitor your open trades and portfolio performance.</p>
            <div class="loading">Demo positions interface</div>
        </div>
    </div>

    <script>
        // Initialize Telegram WebApp
        window.Telegram.WebApp.ready();
        window.Telegram.WebApp.expand();
        
        let currentSymbol = 'BTCUSDT';
        let priceChart = null;
        
        // Tab switching
        document.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', () => {
                const tabName = tab.dataset.tab;
                
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                
                tab.classList.add('active');
                document.getElementById(tabName).classList.add('active');
            });
        });
        
        // Symbol selection
        document.querySelectorAll('.symbol-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.symbol-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                currentSymbol = btn.dataset.symbol;
                loadMarketData();
                loadChartData();
            });
        });
        
        // Load market data
        async function loadMarketData() {
            try {
                const response = await fetch(`/api/market-data?symbol=${currentSymbol}`);
                const data = await response.json();
                
                console.log('Live market data loaded successfully:', data);
                
                const statsContainer = document.getElementById('market-stats');
                const changeClass = data.change >= 0 ? 'positive' : 'negative';
                const changeSign = data.change >= 0 ? '+' : '';
                
                statsContainer.innerHTML = `
                    <div class="stat-card">
                        <div>Price</div>
                        <div class="stat-value">$${data.price.toLocaleString()}</div>
                    </div>
                    <div class="stat-card">
                        <div>24h Change</div>
                        <div class="stat-value ${changeClass}">
                            ${changeSign}${data.changePercent.toFixed(2)}%
                        </div>
                    </div>
                    <div class="stat-card">
                        <div>24h High</div>
                        <div class="stat-value">$${data.high.toLocaleString()}</div>
                    </div>
                    <div class="stat-card">
                        <div>24h Low</div>
                        <div class="stat-value">$${data.low.toLocaleString()}</div>
                    </div>
                `;
            } catch (error) {
                console.error('Error loading market data:', error);
            }
        }
        
        // Load chart data
        async function loadChartData() {
            try {
                const response = await fetch(`/api/kline-data?symbol=${currentSymbol}&interval=1h&limit=50`);
                const data = await response.json();
                
                console.log('Live chart data loaded for', currentSymbol + ':', data.length, 'candles');
                
                if (priceChart) {
                    priceChart.destroy();
                }
                
                const ctx = document.getElementById('priceChart').getContext('2d');
                priceChart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        datasets: [{
                            label: currentSymbol + ' Price',
                            data: data.map(candle => ({
                                x: candle.x,
                                y: candle.c
                            })),
                            borderColor: '#1e3c72',
                            backgroundColor: 'rgba(30, 60, 114, 0.1)',
                            tension: 0.4,
                            fill: true
                        }]
                    },
                    options: {
                        responsive: true,
                        scales: {
                            x: {
                                type: 'time',
                                time: {
                                    unit: 'hour'
                                }
                            },
                            y: {
                                beginAtZero: false
                            }
                        },
                        plugins: {
                            legend: {
                                display: false
                            }
                        }
                    }
                });
            } catch (error) {
                console.error('Error loading chart data:', error);
            }
        }
        
        // Initial load
        loadMarketData();
        loadChartData();
        
        // Auto refresh every 30 seconds
        setInterval(() => {
            loadMarketData();
            loadChartData();
        }, 30000);
    </script>
</body>
</html>'''

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'timestamp': datetime.utcnow().isoformat()})

@app.route('/api/market-data')
def market_data():
    """Fetch live market data from Binance API"""
    try:
        symbol = request.args.get('symbol', 'BTCUSDT')
        url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
        
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())
            
        market_info = {
            'symbol': data['symbol'],
            'price': float(data['lastPrice']),
            'change': float(data['priceChange']),
            'changePercent': float(data['priceChangePercent']),
            'high': float(data['highPrice']),
            'low': float(data['lowPrice']),
            'volume': float(data['volume']),
            'quoteVolume': float(data['quoteVolume']),
            'openPrice': float(data['openPrice']),
            'timestamp': int(time.time() * 1000)
        }
        
        return jsonify(market_info)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/kline-data')
def kline_data():
    """Fetch live kline/candlestick data from Binance API"""
    try:
        symbol = request.args.get('symbol', 'BTCUSDT')
        interval = request.args.get('interval', '1h')
        limit = request.args.get('limit', '50')
        
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())
        
        # Convert to Chart.js format
        chart_data = []
        for kline in data:
            chart_data.append({
                'x': int(kline[0]),  # timestamp
                'o': float(kline[1]),  # open
                'h': float(kline[2]),  # high
                'l': float(kline[3]),  # low
                'c': float(kline[4])   # close
            })
        
        return jsonify(chart_data)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Vercel handler function
def handler(request, response):
    """Handle Vercel serverless function requests"""
    from werkzeug.serving import WSGIRequestHandler
    from werkzeug.wrappers import Request, Response
    
    # Convert Vercel request to WSGI environ
    environ = {
        'REQUEST_METHOD': request.method,
        'PATH_INFO': request.path,
        'QUERY_STRING': request.query,
        'CONTENT_TYPE': request.headers.get('content-type', ''),
        'CONTENT_LENGTH': request.headers.get('content-length', ''),
        'HTTP_HOST': request.headers.get('host', ''),
        'wsgi.version': (1, 0),
        'wsgi.url_scheme': 'https',
        'wsgi.input': request.body,
        'wsgi.errors': None,
        'wsgi.multithread': False,
        'wsgi.multiprocess': True,
        'wsgi.run_once': False,
    }
    
    # Add HTTP headers
    for key, value in request.headers.items():
        key = 'HTTP_' + key.upper().replace('-', '_')
        environ[key] = value
    
    # Response handling
    response_data = []
    def start_response(status, headers):
        response.status = int(status.split()[0])
        for header_name, header_value in headers:
            response.headers[header_name] = header_value
    
    # Call Flask app
    app_response = app(environ, start_response)
    
    # Return response body
    return b''.join(app_response)