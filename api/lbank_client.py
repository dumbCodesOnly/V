"""
LBank API Client - Comprehensive futures trading integration
Reference: https://www.lbank.com/docs/index.html
GitHub: https://github.com/LBank-exchange/lbank-official-api-docs
"""

import hashlib
import hmac
import logging
import requests
import time
from typing import Optional, Dict, List
from urllib.parse import urlencode
import json

from config import APIConfig, TradingConfig


class LBankClient:
    """LBank API client following official documentation specifications"""
    
    def __init__(self, api_key: str, api_secret: str, passphrase: str = "", testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        
        # Official LBank API endpoints
        self.base_url = "https://api.lbkex.com"
        self.futures_base = "/v2/futures"  # Futures trading
        self.spot_base = "/v2"             # Spot trading
        
        # Track last error for better user feedback
        self.last_error = None
        
        # LBank supports testnet, but we'll use mainnet for production
        if testnet:
            logging.info("LBank testnet mode requested - using mainnet for production trading")
        
        # Request session for connection pooling
        self.session = requests.Session()
        
    def _generate_signature(self, query_string: str) -> str:
        """
        Generate MD5 signature according to LBank specification
        
        LBank uses MD5 hash for signature generation:
        1. Sort all parameters alphabetically
        2. Create query string: param1=value1&param2=value2
        3. Append secret key: query_string + secret_key
        4. Generate MD5 hash
        """
        # Append secret key to query string for MD5 hash
        string_to_sign = query_string + self.api_secret
        return hashlib.md5(string_to_sign.encode('utf-8')).hexdigest()
    
    def get_server_time(self) -> int:
        """Get server time from LBank for accurate timestamp synchronization"""
        try:
            # Public endpoint for server time
            response = self.session.get(f"{self.base_url}/v2/accuracy.do", timeout=5)
            if response.status_code == 200:
                data = response.json()
                # LBank returns timestamp in seconds, convert to milliseconds
                return int(data.get('timestamp', time.time()) * 1000)
        except Exception as e:
            logging.debug(f"Failed to get LBank server time: {e}")
        
        # Fallback to local time in milliseconds
        return int(time.time() * 1000)
    
    def _signed_request(self, method: str, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """
        Make signed request following LBank official specifications
        
        LBank Authentication Process:
        1. Add api_key and timestamp to parameters
        2. Sort parameters alphabetically
        3. Create query string
        4. Append secret key and generate MD5 signature
        5. Add signature to parameters
        """
        if params is None:
            params = {}
        
        # Add required api_key and timestamp
        params['api_key'] = self.api_key
        params['timestamp'] = str(int(time.time() * 1000))
        
        # Sort parameters alphabetically (required by LBank)
        sorted_params = dict(sorted(params.items()))
        
        # Create query string for signature generation
        query_string = '&'.join([f"{k}={v}" for k, v in sorted_params.items()])
        
        # Generate signature
        signature = self._generate_signature(query_string)
        
        # Add signature to parameters
        sorted_params['sign'] = signature
        
        # Prepare headers
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json'
        }
        
        # Build full URL
        url = f"{self.base_url}{endpoint}"
        
        # Log API call for debugging
        logging.debug(f"LBank {method} {endpoint}")
        
        try:
            if method == 'GET':
                response = self.session.get(url, params=sorted_params, headers=headers, timeout=10)
            elif method == 'POST':
                response = self.session.post(url, data=sorted_params, headers=headers, timeout=10)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            if response.status_code == 200:
                result = response.json()
                # Check LBank's result structure
                if 'result' in result and result['result'] == 'true':
                    logging.debug(f"LBank API success: {endpoint}")
                    return result.get('data', result)
                elif 'error_code' in result:
                    error_msg = result.get('error_code', 'Unknown error')
                    logging.error(f"LBank API error: {error_msg}")
                    self.last_error = f"LBank Error: {error_msg}"
                    return None
                else:
                    return result
            else:
                error_text = response.text
                logging.error(f"LBank API HTTP error {response.status_code}: {error_text}")
                self.last_error = f"HTTP {response.status_code}: {error_text}"
                return None
                
        except Exception as e:
            logging.error(f"LBank API request failed: {str(e)}")
            self.last_error = f"Request failed: {str(e)}"
            return None
    
    def _public_request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make public (unsigned) request to LBank"""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.get(url, params=params, timeout=10)
            if response.status_code == 200:
                result = response.json()
                # Handle LBank response format
                if isinstance(result, dict) and 'data' in result:
                    return result['data']
                return result
            return None
        except Exception as e:
            logging.error(f"LBank public request failed: {e}")
            return None
    
    # Market Data Methods
    def get_ticker(self, symbol: str) -> Optional[Dict]:
        """Get 24hr ticker price change statistics"""
        lbank_symbol = self.convert_to_lbank_symbol(symbol)
        result = self._public_request("/v2/ticker.do", {"symbol": lbank_symbol})
        if isinstance(result, list) and len(result) > 0:
            return result[0]  # LBank returns array, get first item
        return result
    
    def get_ticker_price(self, symbol: str) -> Optional[float]:
        """Get current ticker price for a symbol"""
        try:
            ticker = self.get_ticker(symbol)
            if ticker and 'ticker' in ticker:
                return float(ticker['ticker'].get('latest', 0))
            elif ticker and 'latest' in ticker:
                return float(ticker['latest'])
            return None
        except Exception as e:
            logging.warning(f"Failed to get LBank ticker price for {symbol}: {e}")
            return None
    
    def get_exchange_info(self) -> Optional[Dict]:
        """Get exchange information and trading pairs"""
        return self._public_request("/v2/currencyPairs.do")
    
    # Account Methods
    def get_account_balance(self) -> List[Dict]:
        """Get futures account balance"""
        result = self._signed_request('POST', "/v2/user_info.do")
        if result and 'info' in result:
            # Convert LBank balance format to match Toobit structure
            balances = []
            for asset, balance_data in result['info'].items():
                if isinstance(balance_data, dict):
                    balances.append({
                        'asset': asset.upper(),
                        'balance': str(balance_data.get('total', '0')),
                        'availableBalance': str(balance_data.get('free', '0')),
                        'positionMargin': str(balance_data.get('locked', '0')),
                        'orderMargin': '0',  # LBank doesn't separate order margin
                        'crossUnRealizedPnl': '0'  # Will be calculated separately
                    })
            return balances
        return []
    
    def get_positions(self) -> List[Dict]:
        """Get all open positions"""
        # LBank futures positions endpoint
        result = self._signed_request('POST', f"{self.futures_base}/positions.do")
        if result and isinstance(result, list):
            return result
        elif result and 'positions' in result:
            return result['positions']
        return []
    
    def get_position(self, symbol: str) -> Optional[Dict]:
        """Get specific position by symbol"""
        positions = self.get_positions()
        if positions:
            lbank_symbol = self.convert_to_lbank_symbol(symbol)
            for pos in positions:
                if pos.get('symbol', '').upper() == lbank_symbol.upper():
                    return pos
        return None
    
    # Order Methods
    def place_order(self, symbol: str, side: str, order_type: str, quantity: str, 
                   price: Optional[str] = None, **kwargs) -> Optional[Dict]:
        """
        Place a new order following LBank specifications
        
        LBank order parameters:
        - symbol: Trading pair (e.g., btc_usdt)
        - type: buy/sell
        - amount: Order quantity
        - price: Order price (for limit orders)
        """
        try:
            lbank_symbol = self.convert_to_lbank_symbol(symbol)
            
            # Map order parameters to LBank format
            params = {
                'symbol': lbank_symbol,
                'type': side.lower(),  # buy/sell
                'amount': quantity
            }
            
            # Add price for limit orders
            if order_type.upper() == 'LIMIT' and price:
                params['price'] = price
            
            # Place order via LBank API
            result = self._signed_request('POST', "/v2/create_order.do", params)
            
            if result and 'order_id' in result:
                logging.info(f"LBank order placed successfully: {result['order_id']}")
                return {
                    'orderId': str(result['order_id']),
                    'symbol': symbol,
                    'side': side,
                    'type': order_type,
                    'quantity': quantity,
                    'price': price,
                    'status': 'NEW'
                }
            
            return result
            
        except Exception as e:
            logging.error(f"LBank place_order failed: {e}")
            self.last_error = f"Order placement failed: {str(e)}"
            return None
    
    def cancel_order(self, symbol: str, order_id: str) -> Optional[Dict]:
        """Cancel an existing order"""
        try:
            lbank_symbol = self.convert_to_lbank_symbol(symbol)
            params = {
                'symbol': lbank_symbol,
                'order_id': order_id
            }
            
            result = self._signed_request('POST', "/v2/cancel_order.do", params)
            return result
            
        except Exception as e:
            logging.error(f"LBank cancel_order failed: {e}")
            return None
    
    def get_order(self, symbol: str, order_id: str) -> Optional[Dict]:
        """Get order status"""
        try:
            lbank_symbol = self.convert_to_lbank_symbol(symbol)
            params = {
                'symbol': lbank_symbol,
                'order_id': order_id
            }
            
            result = self._signed_request('POST', "/v2/orders_info.do", params)
            return result
            
        except Exception as e:
            logging.error(f"LBank get_order failed: {e}")
            return None
    
    def get_order_history(self, symbol: str, limit: int = 50) -> List[Dict]:
        """Get order history"""
        try:
            lbank_symbol = self.convert_to_lbank_symbol(symbol)
            params = {
                'symbol': lbank_symbol,
                'status': 2,  # All orders
                'current_page': 1,
                'page_length': min(limit, 200)  # LBank max is 200
            }
            
            result = self._signed_request('POST', "/v2/orders_info_history.do", params)
            if result and 'orders' in result:
                return result['orders']
            return result if isinstance(result, list) else []
            
        except Exception as e:
            logging.error(f"LBank get_order_history failed: {e}")
            return []
    
    # Futures-specific methods
    def change_leverage(self, symbol: str, leverage: int) -> Optional[Dict]:
        """Change leverage for futures trading"""
        try:
            lbank_symbol = self.convert_to_lbank_symbol(symbol)
            params = {
                'symbol': lbank_symbol,
                'leverage': str(leverage)
            }
            
            # LBank futures leverage endpoint
            result = self._signed_request('POST', f"{self.futures_base}/leverage.do", params)
            return result
            
        except Exception as e:
            logging.error(f"LBank change_leverage failed: {e}")
            return None
    
    def change_margin_type(self, symbol: str, margin_type: str = "CROSSED") -> Optional[Dict]:
        """Change margin type (ISOLATED/CROSSED)"""
        try:
            lbank_symbol = self.convert_to_lbank_symbol(symbol)
            params = {
                'symbol': lbank_symbol,
                'marginType': margin_type
            }
            
            result = self._signed_request('POST', f"{self.futures_base}/marginType.do", params)
            return result
            
        except Exception as e:
            logging.error(f"LBank change_margin_type failed: {e}")
            return None
    
    def place_futures_trade_with_tp_sl(self, symbol: str, side: str, quantity: str, 
                                      leverage: int, take_profits: List[Dict], 
                                      stop_loss: Optional[Dict] = None) -> Optional[Dict]:
        """
        Place a complete futures trade with take profit and stop loss orders
        This method combines multiple LBank API calls to create a comprehensive trade
        """
        try:
            # 1. Set leverage first
            leverage_result = self.change_leverage(symbol, leverage)
            if not leverage_result:
                logging.error("Failed to set leverage")
                return None
            
            # 2. Place main position order (market order for immediate execution)
            main_order = self.place_order(
                symbol=symbol,
                side=side,
                order_type="MARKET",
                quantity=quantity
            )
            
            if not main_order:
                logging.error("Failed to place main order")
                return None
            
            # 3. Place take profit orders
            tp_orders = []
            for tp in take_profits:
                tp_side = "SELL" if side.upper() == "BUY" else "BUY"
                tp_order = self.place_order(
                    symbol=symbol,
                    side=tp_side,
                    order_type="LIMIT",
                    quantity=str(float(quantity) * tp['allocation'] / 100),
                    price=str(tp['price'])
                )
                if tp_order:
                    tp_orders.append(tp_order)
            
            # 4. Place stop loss order if provided
            sl_order = None
            if stop_loss:
                sl_side = "SELL" if side.upper() == "BUY" else "BUY"
                sl_order = self.place_order(
                    symbol=symbol,
                    side=sl_side,
                    order_type="STOP_LOSS",
                    quantity=quantity,
                    price=str(stop_loss['price'])
                )
            
            return {
                'main_order': main_order,
                'take_profit_orders': tp_orders,
                'stop_loss_order': sl_order,
                'symbol': symbol,
                'side': side,
                'quantity': quantity,
                'leverage': leverage
            }
            
        except Exception as e:
            logging.error(f"LBank place_futures_trade_with_tp_sl failed: {e}")
            self.last_error = f"Complete trade placement failed: {str(e)}"
            return None
    
    # Symbol conversion methods
    def convert_to_lbank_symbol(self, symbol: str) -> str:
        """Convert standard symbol format to LBank format"""
        # LBank uses lowercase with underscore (e.g., btc_usdt)
        if symbol.upper().endswith('USDT'):
            base = symbol[:-4].lower()
            return f"{base}_usdt"
        elif '_' not in symbol:
            # If no underscore, assume it's a standard format like BTCUSDT
            if len(symbol) >= 6:
                base = symbol[:-4].lower()
                quote = symbol[-4:].lower()
                return f"{base}_{quote}"
        
        # Return as-is if already in correct format or unknown
        return symbol.lower()
    
    def convert_from_lbank_symbol(self, lbank_symbol: str) -> str:
        """Convert LBank symbol format to standard format"""
        if '_' in lbank_symbol:
            parts = lbank_symbol.upper().split('_')
            return ''.join(parts)
        return lbank_symbol.upper()