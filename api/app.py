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
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <style>
            body { 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
                color: white; 
                margin: 0; 
                padding: 20px;
                min-height: 100vh;
                box-sizing: border-box;
            }
            .container { max-width: 400px; margin: 0 auto; }
            .header { 
                text-align: center; 
                margin-bottom: 30px;
                background: rgba(255,255,255,0.1);
                padding: 20px;
                border-radius: 15px;
                backdrop-filter: blur(10px);
            }
            .header h1 { margin: 0; font-size: 24px; }
            .header p { margin: 10px 0 0; opacity: 0.8; }
            
            .tabs {
                display: flex;
                background: rgba(255,255,255,0.1);
                border-radius: 15px;
                margin-bottom: 20px;
                padding: 5px;
            }
            .tab {
                flex: 1;
                padding: 12px;
                text-align: center;
                border-radius: 10px;
                cursor: pointer;
                transition: all 0.3s;
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
            .tab-content.active { display: block; }
            
            .stats { 
                display: grid; 
                grid-template-columns: 1fr 1fr; 
                gap: 15px; 
                margin-bottom: 20px;
            }
            .stat { 
                background: rgba(255,255,255,0.15); 
                padding: 15px; 
                border-radius: 12px; 
                text-align: center;
                border: 1px solid rgba(255,255,255,0.2);
            }
            .stat div:first-child { 
                font-size: 12px; 
                opacity: 0.8; 
                margin-bottom: 5px; 
            }
            .stat div:last-child { 
                font-size: 18px; 
                font-weight: bold; 
            }
            .positive { color: #4CAF50; }
            .negative { color: #f44336; }
            
            .feature-grid {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 15px;
                margin-top: 20px;
            }
            .feature {
                background: rgba(255,255,255,0.15);
                padding: 20px;
                border-radius: 12px;
                text-align: center;
                border: 1px solid rgba(255,255,255,0.2);
            }
            .feature h3 { margin: 0 0 10px; font-size: 16px; }
            .feature p { margin: 0; font-size: 14px; opacity: 0.8; }
            
            .status-indicator {
                display: inline-block;
                width: 8px;
                height: 8px;
                border-radius: 50%;
                margin-right: 8px;
            }
            .status-live { background: #4CAF50; }
            .status-demo { background: #FF9800; }
            .status-error { background: #f44336; }
            
            .chart-container {
                background: rgba(255,255,255,0.1);
                border-radius: 12px;
                padding: 20px;
                margin-top: 20px;
                text-align: center;
            }
            
            .trading-section {
                margin-top: 20px;
            }
            .trade-item {
                background: rgba(255,255,255,0.1);
                border-radius: 12px;
                padding: 15px;
                margin-bottom: 15px;
                border: 1px solid rgba(255,255,255,0.2);
            }
            .trade-item h4 { margin: 0 0 10px; }
            .trade-item p { margin: 5px 0; font-size: 14px; opacity: 0.9; }
            
            @media (max-width: 480px) {
                .stats { grid-template-columns: 1fr; }
                .feature-grid { grid-template-columns: 1fr; }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🤖 Toobit Trading Bot</h1>
                <p><span id="status-indicator" class="status-indicator status-demo"></span>Multi-Trade Management System</p>
            </div>
            
            <div class="tabs">
                <div class="tab active" onclick="showTab('market')">Market</div>
                <div class="tab" onclick="showTab('trading')">Trading</div>
                <div class="tab" onclick="showTab('positions')">Positions</div>
            </div>
            
            <div id="market-tab" class="tab-content active">
                <div class="stats" id="stats">
                    <div class="stat">
                        <div>Loading...</div>
                        <div>Please wait</div>
                    </div>
                </div>
                
                <div class="chart-container">
                    <h3>Price Chart</h3>
                    <p>📈 Real-time BTC/USDT data</p>
                    <p><small>Updates every 30 seconds</small></p>
                </div>
            </div>
            
            <div id="trading-tab" class="tab-content">
                <div class="feature-grid">
                    <div class="feature">
                        <h3>⚡ Quick Trade</h3>
                        <p>Fast market orders</p>
                    </div>
                    <div class="feature">
                        <h3>🎯 Strategy</h3>
                        <p>Multi-level TP/SL</p>
                    </div>
                    <div class="feature">
                        <h3>📊 Analysis</h3>
                        <p>Risk management</p>
                    </div>
                    <div class="feature">
                        <h3>🔄 Auto Trade</h3>
                        <p>Set and forget</p>
                    </div>
                </div>
                
                <div class="trading-section">
                    <h3>Active Configurations</h3>
                    <div class="trade-item">
                        <h4>BTC/USDT Long Strategy</h4>
                        <p>Entry: $119,500 | TP1: $121,000 (30%)</p>
                        <p>TP2: $122,500 (40%) | TP3: $124,000 (30%)</p>
                        <p>Status: <span style="color: #FF9800">Configured</span></p>
                    </div>
                </div>
            </div>
            
            <div id="positions-tab" class="tab-content">
                <div class="feature">
                    <h3>📊 Portfolio Overview</h3>
                    <p>Total Balance: $10,000 (Demo)</p>
                    <p>Available: $8,500 | In Use: $1,500</p>
                    <p>Today's P&L: <span class="positive">+$127.50 (+1.28%)</span></p>
                </div>
                
                <div class="trading-section">
                    <h3>Open Positions</h3>
                    <div class="trade-item">
                        <h4>BTC/USDT Long</h4>
                        <p>Size: 0.025 BTC | Entry: $119,750</p>
                        <p>Current: $120,186 | P&L: <span class="positive">+$10.90</span></p>
                        <p>ROE: <span class="positive">+0.87%</span></p>
                    </div>
                </div>
            </div>
        </div>
        
        <script>
            let currentTab = 'market';
            
            function showTab(tabName) {
                // Hide all tabs
                document.querySelectorAll('.tab-content').forEach(tab => {
                    tab.classList.remove('active');
                });
                document.querySelectorAll('.tab').forEach(tab => {
                    tab.classList.remove('active');
                });
                
                // Show selected tab
                document.getElementById(tabName + '-tab').classList.add('active');
                event.target.classList.add('active');
                currentTab = tabName;
            }
            
            async function loadMarketData() {
                try {
                    const response = await fetch('/api/market');
                    const data = await response.json();
                    
                    if (data.error) {
                        throw new Error(data.error);
                    }
                    
                    const changeClass = data.change >= 0 ? 'positive' : 'negative';
                    const source = data.source || 'unknown';
                    const isLive = source !== 'demo';
                    
                    // Update status indicator
                    const indicator = document.getElementById('status-indicator');
                    indicator.className = `status-indicator ${isLive ? 'status-live' : 'status-demo'}`;
                    
                    document.getElementById('stats').innerHTML = `
                        <div class="stat">
                            <div>BTC Price</div>
                            <div>$${data.price.toLocaleString()}</div>
                        </div>
                        <div class="stat">
                            <div>24h Change</div>
                            <div class="${changeClass}">${data.changePercent.toFixed(2)}%</div>
                        </div>
                        <div class="stat">
                            <div>24h Volume</div>
                            <div>$2.1B</div>
                        </div>
                        <div class="stat">
                            <div>Data Source</div>
                            <div>${source.toUpperCase()}</div>
                        </div>
                        <div class="stat">
                            <div>Status</div>
                            <div>${isLive ? 'Live Data' : 'Demo Mode'}</div>
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
                            <div>Connection</div>
                            <div style="color: #f44336;">Failed</div>
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
                    
                    // Update status indicator to error
                    const indicator = document.getElementById('status-indicator');
                    indicator.className = 'status-indicator status-error';
                }
            }
            
            // Initialize Telegram WebApp
            if (window.Telegram && window.Telegram.WebApp) {
                window.Telegram.WebApp.ready();
                window.Telegram.WebApp.expand();
                window.Telegram.WebApp.MainButton.hide();
            }
            
            // Load data immediately and then every 30 seconds
            loadMarketData();
            setInterval(loadMarketData, 30000);
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

@app.route('/api/trading')
def trading():
    """Get trading configurations"""
    return jsonify({
        'trades': [
            {
                'id': 1,
                'symbol': 'BTCUSDT',
                'side': 'LONG',
                'entry_price': 119500,
                'take_profits': [
                    {'level': 1, 'price': 121000, 'percentage': 30},
                    {'level': 2, 'price': 122500, 'percentage': 40},
                    {'level': 3, 'price': 124000, 'percentage': 30}
                ],
                'stop_loss': 117000,
                'status': 'configured'
            }
        ]
    })

@app.route('/api/positions')
def positions():
    """Get current positions"""
    return jsonify({
        'positions': [
            {
                'symbol': 'BTCUSDT',
                'side': 'LONG',
                'size': 0.025,
                'entry_price': 119750,
                'current_price': 120186,
                'pnl': 10.90,
                'roe': 0.87,
                'margin': 1500
            }
        ],
        'portfolio': {
            'total_balance': 10000,
            'available': 8500,
            'in_use': 1500,
            'daily_pnl': 127.50,
            'daily_roe': 1.28
        }
    })

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

# For Vercel
application = app

if __name__ == '__main__':
    app.run(debug=True)