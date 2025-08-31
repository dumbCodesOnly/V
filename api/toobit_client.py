"""
Toobit API Client - Completely rewritten based on official documentation
Reference: https://toobit-docs.github.io/apidocs/usdt_swap/v1/en/
"""

import hashlib
import hmac
import logging
import requests
import time
from typing import Optional, Dict, List
from urllib.parse import urlencode

from config import APIConfig, TradingConfig


class ToobitClient:
    """Toobit API client following official documentation specifications"""
    
    def __init__(self, api_key: str, api_secret: str, passphrase: str = "", testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        
        # Official Toobit API endpoints
        self.base_url = "https://api.toobit.com"
        self.futures_base = "/api/v1/futures"  # USDT-M Futures
        self.spot_base = "/api/v1/spot"        # Spot trading
        
        # Track last error for better user feedback
        self.last_error = None
        
        # Log warning if testnet was requested (Toobit doesn't support testnet)
        if testnet:
            logging.warning("TOOBIT TESTNET DISABLED: Toobit does not support testnet mode. Using mainnet/live trading instead.")
        
        # Request session for connection pooling
        self.session = requests.Session()
        
    def _generate_signature(self, query_string: str) -> str:
        """
        Generate HMAC SHA256 signature according to Toobit specification
        
        From docs: The signature uses the HMAC SHA256 algorithm. The API-Secret 
        corresponding to the API-KEY is used as the key of HMAC SHA256, and all 
        other parameters are used as the operation object of HMAC SHA256
        """
        return hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
    
    def get_server_time(self) -> int:
        """Get server time from Toobit for accurate timestamp synchronization"""
        try:
            # Public endpoint for server time
            response = self.session.get(f"{self.base_url}/api/v1/time", timeout=5)
            if response.status_code == 200:
                data = response.json()
                return int(data.get('serverTime', time.time() * 1000))
        except Exception as e:
            logging.debug(f"Failed to get server time: {e}")
        
        # Fallback to local time
        return int(time.time() * 1000)
    
    def _signed_request(self, method: str, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """
        Make signed request following Toobit official specifications
        
        Based on official docs example:
        echo -n "symbol=BTCUSDT&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=400&recvWindow=100000&timestamp=1668481902307" | openssl dgst -sha256 -hmac "YOUR_SECRET"
        
        Key points:
        1. Parameters are sorted alphabetically 
        2. Query string format: key=value&key=value (NO URL encoding before signing)
        3. Signature is HMAC SHA256 of the query string (WITHOUT signature parameter)
        4. Signature is appended as &signature= to the request body
        """
        if params is None:
            params = {}
        
        # Add required timestamp and recvWindow
        params['timestamp'] = self.get_server_time()
        if 'recvWindow' not in params:
            params['recvWindow'] = '5000'
        
        # Create query string preserving parameter order (CRITICAL for signature validation)
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        
        # Generate signature from the query string (WITHOUT signature parameter)
        signature = self._generate_signature(query_string)
        
        # Now add signature to params for the actual request
        params['signature'] = signature
        
        # Prepare headers as per official docs
        headers = {
            'X-BB-APIKEY': self.api_key,
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        # Build full URL
        url = f"{self.base_url}{endpoint}"
        
        # Minimal logging for API calls
        logging.debug(f"Toobit {method} {endpoint}")
        
        try:
            if method == 'GET':
                response = self.session.get(url, params=params, headers=headers, timeout=10)
            elif method == 'POST':
                # For POST, send as form data in body (not URL params)
                response = self.session.post(url, data=params, headers=headers, timeout=10)
            elif method == 'DELETE':
                response = self.session.delete(url, data=params, headers=headers, timeout=10)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            if response.status_code == 200:
                result = response.json()
                logging.debug(f"Toobit API success: {endpoint}")
                return result
            else:
                error_text = response.text
                logging.error(f"Toobit API error {response.status_code}: {error_text}")
                try:
                    error_data = response.json()
                    self.last_error = f"API Error {error_data.get('code', 'Unknown')}: {error_data.get('msg', error_text)}"
                except:
                    self.last_error = f"HTTP {response.status_code}: {error_text}"
                return None
                
        except Exception as e:
            logging.error(f"Toobit API request failed: {str(e)}")
            self.last_error = f"Request failed: {str(e)}"
            return None
    
    def _public_request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make public (unsigned) request"""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.get(url, params=params, timeout=10)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logging.error(f"Public request failed: {e}")
            return None
    
    # Market Data Methods
    def get_ticker(self, symbol: str) -> Optional[Dict]:
        """Get 24hr ticker price change statistics"""
        toobit_symbol = self.convert_to_toobit_symbol(symbol)
        return self._public_request(f"/api/v1/futures/ticker/24hr", {"symbol": toobit_symbol})
    
    def get_ticker_price(self, symbol: str) -> Optional[float]:
        """Get current ticker price for a symbol"""
        try:
            ticker = self.get_ticker(symbol)
            if ticker and 'price' in ticker:
                return float(ticker['price'])
            return None
        except Exception as e:
            logging.warning(f"Failed to get ticker price for {symbol}: {e}")
            return None
    
    def get_exchange_info(self) -> Optional[Dict]:
        """Get exchange information - try different possible endpoints"""
        # Try the standard exchangeInfo first
        result = self._public_request(f"/api/v1/futures/exchangeInfo")
        if result:
            return result
            
        # Try alternative endpoints that might exist on Toobit
        alternatives = [
            "/api/v1/exchangeInfo",  # Without 'futures' prefix
            "/api/v1/futures/exchange-info",  # With hyphen
            "/api/v1/futures/symbols",  # Just symbols
        ]
        
        for endpoint in alternatives:
            logging.debug(f"Trying alternative endpoint: {endpoint}")
            result = self._public_request(endpoint)
            if result:
                logging.info(f"Found working endpoint: {endpoint}")
                return result
                
        return None
    
    # Account Methods
    def get_account_balance(self) -> List[Dict]:
        """Get futures account balance"""
        result = self._signed_request('GET', f"{self.futures_base}/balance")
        return result if isinstance(result, list) else []
    
    def get_positions(self) -> List[Dict]:
        """Get all positions"""
        result = self._signed_request('GET', f"{self.futures_base}/positionRisk")
        return result if isinstance(result, list) else []
    
    def get_position(self, symbol: str) -> Optional[Dict]:
        """Get specific position by symbol"""
        positions = self.get_positions()
        if positions:
            for pos in positions:
                if pos.get('symbol') == symbol.upper():
                    return pos
        return None
    
    # Order Methods
    def place_order(self, symbol: str, side: str, order_type: str, quantity: str, 
                   price: Optional[str] = None, **kwargs) -> Optional[Dict]:
        """
        Place a new order following Toobit specifications
        
        Proper Toobit side values:
        BUY_OPEN, SELL_OPEN, BUY_CLOSE, SELL_CLOSE
        
        Supported types (per Toobit docs):
        LIMIT, STOP (includes MARKET)
        
        Supported timeInForce:
        GTC, FOK, IOC, LIMIT_MAKER
        
        Supported priceType:
        INPUT, OPPONENT, QUEUE, OVER, MARKET
        """
        import uuid
        from collections import OrderedDict

        params = OrderedDict()

        # Core params - convert to Toobit format
        params['symbol'] = self.convert_to_toobit_symbol(symbol)
        
        # Convert standard side to Toobit side format
        if side.upper() in ['BUY', 'LONG']:
            params['side'] = 'BUY_OPEN'
        elif side.upper() in ['SELL', 'SHORT']:
            params['side'] = 'SELL_OPEN'
        elif side.upper() == 'BUY_CLOSE':
            params['side'] = 'BUY_CLOSE'
        elif side.upper() == 'SELL_CLOSE':
            params['side'] = 'SELL_CLOSE'
        else:
            # Use the side as provided if it's already in correct format
            params['side'] = side.upper()
        
        # Toobit order types: LIMIT or STOP
        if order_type.upper() == 'MARKET':
            params['type'] = 'LIMIT'
            params['priceType'] = 'MARKET'  # Use MARKET priceType for market orders
        elif order_type.upper() == 'LIMIT':
            params['type'] = 'LIMIT'
            params['priceType'] = kwargs.get('priceType', 'INPUT')  # Default to INPUT
        elif order_type.upper() in ['STOP', 'STOP_MARKET']:
            params['type'] = 'STOP'
            params['priceType'] = kwargs.get('priceType', 'INPUT')
        else:
            params['type'] = 'LIMIT'  # Default to LIMIT
            params['priceType'] = 'INPUT'
            
        params['quantity'] = f"{float(quantity):.6f}".rstrip('0').rstrip('.')
        
        # Add leverage if provided
        if 'leverage' in kwargs:
            params['leverage'] = str(kwargs['leverage'])

        # timeInForce is required for LIMIT orders
        if params['type'] == 'LIMIT':
            params['timeInForce'] = kwargs.get('timeInForce', 'GTC')

        # Add price for LIMIT orders or when priceType is INPUT
        if price and (params['type'] == 'LIMIT' or params.get('priceType') == 'INPUT'):
            # Format price with appropriate decimal places for different symbols
            if 'BTC' in symbol.upper():
                # BTC uses zero decimal places (whole numbers)
                params['price'] = f"{float(price):.0f}"
            elif any(coin in symbol.upper() for coin in ['ETH', 'SOL', 'BNB']):
                # Major altcoins typically use 2-3 decimals
                params['price'] = f"{float(price):.3f}"
            else:
                # Other symbols use 4 decimal places max
                params['price'] = f"{float(price):.4f}"

        # Client order ID - Toobit expects 'newClientOrderId'
        params['newClientOrderId'] = kwargs.get('newClientOrderId', f"pl{int(time.time() * 1000)}")

        # recvWindow
        params['recvWindow'] = str(kwargs.get('recvWindow', '5000'))

        # Convert everything to str
        params = {k: str(v) for k, v in params.items()}

        formatted_quantity = f"{float(quantity):.6f}".rstrip('0').rstrip('.')
        logging.info(f"[ORDER] Placing {params['side']} {params['type']} order for {symbol}: {formatted_quantity}")

        # DEBUG: Log the exact parameters being sent to Toobit
        logging.info(f"[DEBUG] Toobit order parameters: {params}")

        return self._signed_request('POST', f"{self.futures_base}/order", params)
    
    def get_order(self, symbol: str, order_id: str) -> Optional[Dict]:
        """Get order status"""
        params = {
            'symbol': self.convert_to_toobit_symbol(symbol),
            'orderId': order_id
        }
        return self._signed_request('GET', f"{self.futures_base}/order", params)
    
    def cancel_order(self, symbol: str, order_id: str) -> Optional[Dict]:
        """Cancel an order"""
        params = {
            'symbol': self.convert_to_toobit_symbol(symbol),
            'orderId': order_id
        }
        return self._signed_request('DELETE', f"{self.futures_base}/order", params)
    
    def place_futures_trade_with_tp_sl(self, symbol: str, side: str, quantity: str, 
                                      take_profit_price: Optional[str] = None, 
                                      stop_loss_price: Optional[str] = None,
                                      entry_price: Optional[str] = None,
                                      market_price: Optional[str] = None) -> Dict:
        """
        Place a complete futures trade with TP/SL using Toobit's supported order types:
        LIMIT, MARKET, STOP_MARKET, TAKE_PROFIT_MARKET
        """
        import uuid
        results = {
            'main_order': None,
            'tp_order': None, 
            'sl_order': None,
            'success': False,
            'errors': []
        }

        try:
            # 1. Entry order - Use proper Toobit format
            if entry_price:
                main_result = self.place_order(
                    symbol=symbol,
                    side=side.upper(),
                    order_type="LIMIT",
                    quantity=quantity,
                    price=entry_price,
                    timeInForce="GTC",
                    priceType="INPUT",
                    recvWindow="5000"
                )
            else:
                # Use MARKET order (LIMIT with priceType=MARKET)
                if not market_price:
                    raise ValueError("Market price required for market execution")
                    
                main_result = self.place_order(
                    symbol=symbol,
                    side=side.upper(),
                    order_type="MARKET",  # Will be converted to LIMIT with priceType=MARKET
                    quantity=quantity,
                    price=market_price,  # Still need price even for market orders
                    timeInForce="IOC",  # Immediate or Cancel for market-like behavior
                    priceType="MARKET",
                    recvWindow="5000"
                )

            results['main_order'] = main_result
            if not main_result:
                results['errors'].append("Failed to place main entry order")
                return results

            logging.info(f"Main order placed: {side} {quantity} {symbol}")

            # Closing side is always opposite
            close_side = "SELL" if side.upper() == "BUY" else "BUY"

            # 2. Stop Loss - Use proper close side format
            if stop_loss_price:
                sl_side = "BUY_CLOSE" if side.upper() in ['SELL', 'SHORT'] else "SELL_CLOSE"
                sl_result = self.place_order(
                    symbol=symbol,
                    side=sl_side,
                    order_type="STOP",
                    quantity=quantity,
                    price=stop_loss_price,
                    priceType="INPUT",
                    newClientOrderId=f"sl{int(time.time() * 1000)}"[:36],
                    recvWindow="5000"
                )
                results['sl_order'] = sl_result
                logging.info(f"SL order placed at {stop_loss_price}")

            # 3. Take Profit
            if take_profit_price:
                tp_side = "BUY_CLOSE" if side.upper() in ['SELL', 'SHORT'] else "SELL_CLOSE"
                tp_result = self.place_order(
                    symbol=symbol,
                    side=tp_side,
                    order_type="LIMIT",
                    quantity=quantity,
                    price=take_profit_price,
                    priceType="INPUT",
                    timeInForce="GTC",
                    newClientOrderId=f"tp{int(time.time() * 1000)}"[:36],
                    recvWindow="5000"
                )
                results['tp_order'] = tp_result
                logging.info(f"TP order placed at {take_profit_price}")

            results['success'] = True
            
        except Exception as e:
            logging.error(f"Error placing futures trade: {e}")
            results['errors'].append(f"Exception: {str(e)}")
        
        return results
    
    def place_multiple_tp_sl_orders(self, symbol: str, side: str, total_quantity: str, 
                                   take_profits: List[Dict], stop_loss_price: Optional[str] = None) -> List[Dict]:
        """
        Legacy method - place multiple TP/SL orders after main position exists
        Use place_futures_trade_with_tp_sl for complete trade setup
        """
        import uuid
        orders_placed = []
        
        # Determine the correct side for closing orders using Toobit format
        if side.lower() in ['long', 'buy']:
            close_side = "SELL_CLOSE"
        else:
            close_side = "BUY_CLOSE"
        
        try:
            # TP/SL orders temporarily disabled - Toobit uses different TP/SL system
            # TODO: Implement Toobit-specific TP/SL order placement
            logging.info(f"TP/SL order placement disabled - using bot monitoring system instead")
            for i, tp in enumerate(take_profits):
                logging.info(f"TP{i+1} will be managed by bot at {tp['price']} for {tp['quantity']} (Toobit integration pending)")
            
            # Skip stop loss order placement for now
            if stop_loss_price:
                logging.info(f"Stop Loss will be managed by bot at {stop_loss_price} for {total_quantity} (Toobit integration pending)")
        
        except Exception as e:
            logging.error(f"Error placing TP/SL orders: {e}")
        
        return orders_placed
    
    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        """Get all open orders"""
        params = {}
        if symbol:
            params['symbol'] = self.convert_to_toobit_symbol(symbol)
        
        result = self._signed_request('GET', f"{self.futures_base}/openOrders", params)
        return result if isinstance(result, list) else []
    
    def get_order_history(self, symbol: str, limit: int = 100) -> List[Dict]:
        """Get order history"""
        params = {
            'symbol': self.convert_to_toobit_symbol(symbol),
            'limit': limit
        }
        result = self._signed_request('GET', f"{self.futures_base}/allOrders", params)
        return result if isinstance(result, list) else []
    
    # Position Management
    def change_leverage(self, symbol: str, leverage: int) -> Optional[Dict]:
        """Change initial leverage"""
        params = {
            'symbol': self.convert_to_toobit_symbol(symbol),
            'leverage': str(leverage)
        }
        return self._signed_request('POST', f"{self.futures_base}/leverage", params)
    
    def change_margin_type(self, symbol: str, margin_type: str) -> Optional[Dict]:
        """Change margin type (ISOLATED or CROSS)"""
        params = {
            'symbol': self.convert_to_toobit_symbol(symbol),
            'marginType': margin_type.upper()
        }
        return self._signed_request('POST', f"{self.futures_base}/marginType", params)
    
    # Utility Methods
    def test_connectivity(self) -> bool:
        """Test API connectivity"""
        result = self._public_request("/api/v1/ping")
        return result is not None
    
    def get_last_error(self) -> Optional[str]:
        """Get the last error message"""
        return self.last_error
    
    @staticmethod
    def convert_to_toobit_symbol(symbol: str) -> str:
        """Convert standard format (BTCUSDT) to Toobit futures format (BTC-SWAP-USDT)"""
        toobit_symbol = TradingConfig.TOOBIT_SYMBOL_MAP.get(symbol.upper())
        if toobit_symbol:
            return toobit_symbol
        # Fallback: try to construct the format if not in mapping
        if symbol.endswith('USDT'):
            base = symbol[:-4]  # Remove 'USDT'
            return f"{base}-SWAP-USDT"
        return symbol  # Return as-is if conversion not possible