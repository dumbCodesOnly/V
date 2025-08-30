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

from config import APIConfig


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
        
        # Debug logging
        logging.info(f"[TOOBIT API] {method} {url}")
        safe_params = {k: v for k, v in params.items() if k != 'signature'}
        logging.info(f"[TOOBIT PARAMS] {safe_params}")
        logging.debug(f"[SIGNATURE] Query: {query_string}")
        logging.debug(f"[SIGNATURE] Hash: {signature}")
        
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
            
            logging.info(f"[TOOBIT RESPONSE] Status: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                logging.info(f"[TOOBIT SUCCESS] {result}")
                return result
            else:
                error_text = response.text
                logging.error(f"[TOOBIT ERROR] {response.status_code}: {error_text}")
                try:
                    error_data = response.json()
                    self.last_error = f"API Error {error_data.get('code', 'Unknown')}: {error_data.get('msg', error_text)}"
                except:
                    self.last_error = f"HTTP {response.status_code}: {error_text}"
                return None
                
        except Exception as e:
            logging.error(f"[TOOBIT EXCEPTION] {str(e)}")
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
        return self._public_request(f"/api/v1/futures/ticker/24hr", {"symbol": symbol})
    
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
        """Get exchange information"""
        return self._public_request(f"/api/v1/futures/exchangeInfo")
    
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
        
        From docs example:
        symbol=BTCUSDT&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=400&recvWindow=100000&timestamp=1668481902307
        """
        # Prepare order parameters in correct order for Toobit signature validation
        import uuid
        from collections import OrderedDict
        
        params = OrderedDict()
        
        # Core parameters (always first)
        params['symbol'] = symbol.upper()
        params['side'] = side.upper()  
        params['type'] = order_type.upper()
        params['quantity'] = f"{float(quantity):.6f}".rstrip('0').rstrip('.')
        
        # Add timeInForce for limit orders only
        if order_type.upper() in ['LIMIT', 'STOP_LIMIT']:
            params['timeInForce'] = kwargs.get('timeInForce', 'GTC')
            
        # Add price for limit orders
        if price and order_type.upper() in ['LIMIT', 'STOP_LIMIT']:
            params['price'] = str(price)
            
        # Add stopPrice if provided (for conditional orders)
        if 'stopPrice' in kwargs:
            stop_price = float(kwargs['stopPrice'])
            params['stopPrice'] = str(stop_price)
            
            # Validate stop price for conditional orders to prevent immediate trigger
            if order_type.upper() in ['STOP', 'STOP_MARKET', 'STOP_LIMIT']:
                current_price = kwargs.get('currentPrice')
                if current_price:
                    current_price = float(current_price)
                    if side.upper() == "BUY" and stop_price <= current_price:
                        logging.warning(f"STOP order validation: BUY stop price {stop_price} <= market price {current_price}")
                    elif side.upper() == "SELL" and stop_price >= current_price:
                        logging.warning(f"STOP order validation: SELL stop price {stop_price} >= market price {current_price}")
                    else:
                        logging.info(f"STOP order validation: Price {stop_price} is valid for {side} order")
        
        # Add margin type for limit orders only
        if order_type.upper() in ['LIMIT', 'STOP_LIMIT']:
            params['marginType'] = kwargs.get('marginType', 'ISOLATED')
            
        # Add reduce only flag if closing position
        if kwargs.get('reduceOnly'):
            params['reduceOnly'] = 'true'
            
        # Add newClientOrderId (required for all orders)
        params['newClientOrderId'] = kwargs.get('newClientOrderId', str(uuid.uuid4())[:36])
        
        # Add recvWindow (always near the end)
        params['recvWindow'] = str(kwargs.get('recvWindow', '5000'))
        
        # Ensure all parameters are strings (required by Toobit signature)
        params = {k: str(v) for k, v in params.items()}
        
        formatted_quantity = f"{float(quantity):.6f}".rstrip('0').rstrip('.')
        logging.info(f"[ORDER] Placing {side} {order_type} order for {symbol}: {formatted_quantity}")
        
        return self._signed_request('POST', f"{self.futures_base}/order", params)
    
    def get_order(self, symbol: str, order_id: str) -> Optional[Dict]:
        """Get order status"""
        params = {
            'symbol': symbol.upper(),
            'orderId': order_id
        }
        return self._signed_request('GET', f"{self.futures_base}/order", params)
    
    def cancel_order(self, symbol: str, order_id: str) -> Optional[Dict]:
        """Cancel an order"""
        params = {
            'symbol': symbol.upper(),
            'orderId': order_id
        }
        return self._signed_request('DELETE', f"{self.futures_base}/order", params)
    
    def place_multiple_tp_sl_orders(self, symbol: str, side: str, total_quantity: str, 
                                   take_profits: List[Dict], stop_loss_price: Optional[str] = None) -> List[Dict]:
        """
        Place multiple take profit and stop loss orders
        
        Args:
            symbol: Trading pair symbol
            side: Original position side ('long' or 'short')
            total_quantity: Total position size
            take_profits: List of TP orders with price, quantity, percentage, allocation
            stop_loss_price: Stop loss price (optional)
        
        Returns:
            List of placed order responses
        """
        import uuid
        orders_placed = []
        
        # Determine the correct side for closing orders (opposite of position side)
        close_side = "SELL" if side.lower() == "long" else "BUY"
        
        try:
            # Place take profit orders
            for i, tp in enumerate(take_profits):
                tp_params = {
                    'newClientOrderId': str(uuid.uuid4())[:36],
                    'reduceOnly': True,
                    'recvWindow': '5000'
                }
                
                order_result = self.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="LIMIT",
                    quantity=tp['quantity'],
                    price=tp['price'],
                    **tp_params
                )
                
                if order_result:
                    orders_placed.append(order_result)
                    logging.info(f"Placed TP{i+1} order: {tp['price']} for {tp['quantity']}")
            
            # Place stop loss order if specified
            if stop_loss_price:
                sl_params = {
                    'newClientOrderId': str(uuid.uuid4())[:36],
                    'stopPrice': stop_loss_price,
                    'reduceOnly': True,
                    'recvWindow': '5000'
                }
                
                sl_result = self.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="STOP_MARKET",
                    quantity=total_quantity,
                    **sl_params
                )
                
                if sl_result:
                    orders_placed.append(sl_result)
                    logging.info(f"Placed SL order: {stop_loss_price} for {total_quantity}")
        
        except Exception as e:
            logging.error(f"Error placing TP/SL orders: {e}")
            # Return partial results if some orders were placed
        
        return orders_placed
    
    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        """Get all open orders"""
        params = {}
        if symbol:
            params['symbol'] = symbol.upper()
        
        result = self._signed_request('GET', f"{self.futures_base}/openOrders", params)
        return result if isinstance(result, list) else []
    
    def get_order_history(self, symbol: str, limit: int = 100) -> List[Dict]:
        """Get order history"""
        params = {
            'symbol': symbol.upper(),
            'limit': limit
        }
        result = self._signed_request('GET', f"{self.futures_base}/allOrders", params)
        return result if isinstance(result, list) else []
    
    # Position Management
    def change_leverage(self, symbol: str, leverage: int) -> Optional[Dict]:
        """Change initial leverage"""
        params = {
            'symbol': symbol.upper(),
            'leverage': str(leverage)
        }
        return self._signed_request('POST', f"{self.futures_base}/leverage", params)
    
    def change_margin_type(self, symbol: str, margin_type: str) -> Optional[Dict]:
        """Change margin type (ISOLATED or CROSS)"""
        params = {
            'symbol': symbol.upper(),
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