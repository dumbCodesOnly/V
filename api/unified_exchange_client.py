"""
Unified Multi-Exchange Client - Consolidated exchange implementation
Supports Toobit and LBank exchanges in a single file for better maintainability
"""

import hashlib
import hmac
import logging
import requests
import time
import inspect
import traceback
from typing import Optional, Dict, List
from urllib.parse import urlencode
import json

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
    
    # Account Methods - Perpetual Futures Only
    
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
    
    def get_futures_balance(self) -> List[Dict]:
        """
        Get futures account balance using Toobit futures API
        
        Official endpoint: GET /api/v1/futures/account
        Returns futures account info with margin and available balances
        """
        try:
            result = self._signed_request('GET', f"{self.futures_base}/account")
            
            if isinstance(result, dict) and 'assets' in result:
                balances = []
                assets = result.get('assets', [])
                if isinstance(assets, list):
                    for asset_data in assets:
                        if isinstance(asset_data, dict):
                            balances.append({
                                'asset': asset_data.get('asset', ''),
                                'balance': asset_data.get('walletBalance', '0'),
                                'availableBalance': asset_data.get('availableBalance', '0'),
                                'positionMargin': asset_data.get('positionInitialMargin', '0'),
                                'orderMargin': asset_data.get('openOrderInitialMargin', '0'),
                                'crossUnRealizedPnl': asset_data.get('crossUnPnl', '0')
                            })
                return balances
            else:
                logging.warning(f"Toobit futures balance fetch failed: {result}")
                return []
                
        except Exception as e:
            logging.error(f"Toobit get_futures_balance error: {e}")
            self.last_error = f"Futures Balance Error: {str(e)}"
            return []
    
    def get_margin_balance(self) -> List[Dict]:
        """
        Get margin account balance using Toobit margin API
        
        Official endpoint: GET /api/v1/margin/account
        Returns margin account info with borrowed amounts and available balances
        """
        try:
            result = self._signed_request('GET', f"/api/v1/margin/account")
            
            if isinstance(result, dict):
                balances = []
                # Handle different response formats
                if 'userAssets' in result:
                    assets = result.get('userAssets', [])
                    if isinstance(assets, list):
                        for asset_data in assets:
                            if isinstance(asset_data, dict):
                                balances.append({
                                    'asset': asset_data.get('asset', ''),
                                    'balance': asset_data.get('free', '0'),
                                    'availableBalance': asset_data.get('free', '0'),
                                    'borrowed': asset_data.get('borrowed', '0'),
                                    'interest': asset_data.get('interest', '0'),
                                    'netAsset': asset_data.get('netAsset', '0')
                                })
                elif 'balances' in result:
                    # Alternative format
                    assets = result.get('balances', [])
                    if isinstance(assets, list):
                        for asset_data in assets:
                            if isinstance(asset_data, dict):
                                balances.append({
                                    'asset': asset_data.get('asset', ''),
                                    'balance': asset_data.get('free', '0'),
                                    'availableBalance': asset_data.get('free', '0'),
                                    'borrowed': '0',  # Default if not provided
                                    'interest': '0',  # Default if not provided
                                    'netAsset': asset_data.get('free', '0')
                                })
                return balances
            else:
                logging.warning(f"Toobit margin balance fetch failed: {result}")
                return []
                
        except Exception as e:
            logging.error(f"Toobit get_margin_balance error: {e}")
            self.last_error = f"Margin Balance Error: {str(e)}"
            return []
    
    def get_api_restrictions(self) -> Optional[Dict]:
        """
        Get API restrictions using Toobit API key permissions endpoint
        
        Official endpoint: GET /api/v1/account/apiRestrictions
        Returns API key permissions and restrictions
        """
        try:
            result = self._signed_request('GET', "/api/v1/account/apiRestrictions")
            
            if isinstance(result, dict):
                logging.info(f"Toobit API restrictions fetched successfully")
                return result
            else:
                logging.warning(f"Toobit API restrictions fetch failed: {result}")
                return None
                
        except Exception as e:
            logging.error(f"Toobit get_api_restrictions error: {e}")
            self.last_error = f"API Restrictions Error: {str(e)}"
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
    
    def get_order_history(self, symbol: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Get order history"""
        params = {'limit': str(limit)}
        if symbol:
            params['symbol'] = self.convert_to_toobit_symbol(symbol)
        
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
    
    def test_connection(self):
        """Test connection and return status with message - used by exchange sync scripts"""
        try:
            if self.test_connectivity():
                return True, "Connection successful"
            else:
                return False, "Connection failed - ping request failed"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
    
    def get_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        """Get orders - alias for get_open_orders used by exchange sync scripts"""
        return self.get_open_orders(symbol)
    
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


class LBankClient:
    """
    LBank API client rewritten from scratch following official documentation
    Using HMAC256 authentication as per LBank specifications
    """
    
    def __init__(self, api_key: str, api_secret: str, passphrase: str = "", testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        
        # Official LBank Perpetual Futures API endpoints
        self.base_url = "https://lbkperp.lbank.com"
        self.public_path = "/cfd/openApi/v1/pub"
        self.private_path = "/cfd/openApi/v1/prv"
        self.futures_base = "/cfd/openApi/v1/prv"  # For compatibility
        
        # Track last error for debugging
        self.last_error = None
        
        # Request session for connection pooling
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'LBank-Python-Client/1.0',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        })
        
    def _generate_echostr(self) -> str:
        """Generate random alphanumeric string (30-40 characters) as required by LBank"""
        import random
        import string
        length = random.randint(30, 40)
        return ''.join(random.choices(string.ascii_letters + string.digits, k=length))
    
    def _get_server_timestamp(self) -> str:
        """Get current timestamp in milliseconds (LBank requirement)"""
        return str(int(time.time() * 1000))
    
    def _detect_signature_method(self) -> str:
        """Auto-detect signature method based on secret key length (CCXT-style)"""
        if len(self.api_secret) > 32:
            return 'RSA'
        else:
            return 'MD5'  # LBank expects "MD5" for HMAC-SHA256 signatures
    
    def _generate_rsa_signature(self, params_string: str) -> str:
        """Generate RSA signature following official LBank connector implementation"""
        try:
            from Crypto.Hash import SHA256
            from Crypto.PublicKey import RSA
            from Crypto.Signature import PKCS1_v1_5
            from base64 import b64encode
            
            # Step 1: Generate MD5 hash of parameter string (UPPERCASE)
            import hashlib
            md5_hash = hashlib.md5(params_string.encode('utf-8')).hexdigest().upper()
            logging.debug(f"LBank RSA - MD5 hash: {md5_hash}")
            
            # Step 2: Format RSA private key
            private_key = (
                "-----BEGIN RSA PRIVATE KEY-----\n"
                + self.api_secret
                + "\n-----END RSA PRIVATE KEY-----"
            )
            
            # Step 3: Sign using RSA PKCS1_v1_5 with SHA256
            pri_key = PKCS1_v1_5.new(RSA.importKey(private_key))
            digest = SHA256.new(md5_hash.encode("utf8"))
            sign = b64encode(pri_key.sign(digest))
            
            signature = sign.decode("utf8")
            logging.debug(f"LBank RSA - Final signature length: {len(signature)}")
            return signature
            
        except Exception as e:
            logging.error(f"LBank RSA signature generation failed: {e}")
            raise
    
    def _generate_hmac_signature(self, params_string: str) -> str:
        """Generate HmacSHA256 signature following official LBank connector implementation"""
        import hmac
        import hashlib
        
        # DEBUG: Log the input parameter string with caller info
        caller_method = inspect.stack()[1].function if len(inspect.stack()) > 1 else 'unknown'
        logging.debug(f"[{caller_method}] LBank HMAC - Input params string: '{params_string}'")
        
        # Step 1: Generate MD5 hash of parameter string (UPPERCASE)
        md5_hash = hashlib.md5(params_string.encode('utf-8')).hexdigest().upper()
        caller_method = inspect.stack()[1].function if len(inspect.stack()) > 1 else 'unknown'
        logging.debug(f"[{caller_method}] LBank HMAC - MD5 hash: {md5_hash}")
        
        # Step 2: Sign MD5 hash using HMAC-SHA256 with secret key (LOWERCASE - per official connector)
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            md5_hash.encode('utf-8'),
            hashlib.sha256
        ).hexdigest().lower()  # Official LBank connector uses .lower(), not .upper()
        
        caller_method = inspect.stack()[1].function if len(inspect.stack()) > 1 else 'unknown'
        logging.debug(f"[{caller_method}] LBank HMAC - Final signature: {signature}")
        return signature
    
    def _generate_signature(self, params_string: str) -> str:
        """Generate signature using auto-detected method (CCXT-style)"""
        signature_method = self._detect_signature_method()
        logging.debug(f"LBank - Auto-detected signature method: {signature_method}")
        
        if signature_method == 'RSA':
            return self._generate_rsa_signature(params_string)
        else:
            return self._generate_hmac_signature(params_string)
    
    def _make_signed_request(self, endpoint: str, params: Optional[Dict] = None, method: str = 'POST') -> Optional[Dict]:
        """
        Make authenticated request to LBank Perpetual Futures API following official documentation
        
        LBank Perpetual Futures Authentication Requirements:
        1. All authenticated requests must be POST with JSON body
        2. Content-Type: application/json
        3. Headers: timestamp, signature_method, echostr
        4. Body: JSON with api_key, signature_method, timestamp, echostr, sign + other params
        """
        if params is None:
            params = {}
        
        # Add required authentication parameters
        timestamp = self._get_server_timestamp()
        echostr = self._generate_echostr()
        
        # Use HmacSHA256 as default signature method for perpetual futures
        signature_method = 'HmacSHA256'
        
        # Create authentication parameters
        auth_params = {
            'api_key': self.api_key,
            'signature_method': signature_method,
            'timestamp': timestamp,
            'echostr': echostr
        }
        
        # Merge with provided parameters
        all_params = {**params, **auth_params}
        
        # Sort parameters alphabetically (critical for signature)
        sorted_params = dict(sorted(all_params.items()))
        
        # Create parameter string for signature (as per documentation)
        param_string = '&'.join([f"{k}={v}" for k, v in sorted_params.items()])
        
        # Generate signature according to documentation:
        # 1. MD5 hash of parameter string (uppercase)
        # 2. Sign the hash with HmacSHA256 (lowercase)
        import hashlib
        md5_hash = hashlib.md5(param_string.encode('utf-8')).hexdigest().upper()
        
        import hmac
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            md5_hash.encode('utf-8'),
            hashlib.sha256
        ).hexdigest().lower()
        
        # Prepare headers according to documentation
        headers = {
            'Content-Type': 'application/json',
            'timestamp': timestamp,
            'signature_method': signature_method,
            'echostr': echostr
        }
        
        # Prepare JSON payload with signature
        payload = dict(sorted_params)
        payload['sign'] = signature
        
        # Make signed request to perpetual futures API
        url = f"{self.base_url}{endpoint}"
        
        # Log request details for debugging
        logging.info(f"LBank Perpetual Futures SIGNED REQUEST: {method} {url}")
        logging.debug(f"LBank Request Headers: {headers}")
        logging.debug(f"LBank Request Payload: {payload}")
        
        try:
            if method.upper() == 'GET':
                # For GET requests, send parameters in URL query string
                response = self.session.get(url, params=payload, headers=headers, timeout=15)
            else:
                # For POST requests, send as JSON body
                response = self.session.post(url, json=payload, headers=headers, timeout=15)
            
            # Log response details
            logging.info(f"LBank Response Status: {response.status_code}")
            
            if response.status_code == 200:
                try:
                    result = response.json()
                    logging.debug(f"LBank Response: {result}")
                    
                    # Handle LBank response format
                    if isinstance(result, dict):
                        if result.get('result') == True or result.get('result') == 'true':
                            logging.info(f"LBank Perpetual Futures API Success for {endpoint}")
                            return result
                        else:
                            error_code = result.get('error_code', 'Unknown error')
                            error_msg = result.get('msg', 'No message')
                            self.last_error = f"LBank Error {error_code}: {error_msg}"
                            logging.error(f"LBank Perpetual Futures API error for {endpoint}: {error_code} - {error_msg}")
                            return result  # Return the error response for handling
                            
                    return result
                except ValueError as json_error:
                    logging.error(f"LBank JSON decode error: {json_error}")
                    logging.error(f"LBank Raw response text: {response.text[:500]}")
                    return None
            else:
                error_text = response.text[:200]
                self.last_error = f"HTTP {response.status_code}: {error_text}"
                logging.error(f"LBank HTTP error {response.status_code} for {endpoint}: {error_text}")
                return None
                
        except Exception as e:
            self.last_error = f"Request failed: {str(e)}"
            logging.error(f"LBank request exception for {endpoint}: {e}")
            return None
    
    def _make_public_request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make public (non-authenticated) request to LBank"""
        url = f"{self.base_url}{endpoint}"
        
        # Log request details for debugging
        logging.info(f"LBank PUBLIC REQUEST: GET {url}")
        if params:
            logging.info(f"LBank Request Params: {params}")
        
        try:
            response = self.session.get(url, params=params, timeout=10)
            
            # Log response details (clean summary for headers)
            logging.info(f"LBank Response Status: {response.status_code}")
            important_headers = {
                k: v for k, v in response.headers.items() 
                if k in ['Content-Type', 'X-LBank-RateLimit-Limit', 'X-LBank-RateLimit-Time', 'Server']
            }
            logging.debug(f"LBank Response Headers: {important_headers}")
            
            if response.status_code == 200:
                try:
                    result = response.json()
                    logging.info(f"LBank Response Body: {str(result)[:500]}{'...' if len(str(result)) > 500 else ''}")
                    logging.info(f"LBank Public API Success for {endpoint}")
                    return result
                except ValueError as json_error:
                    logging.error(f"LBank JSON decode error: {json_error}")
                    logging.error(f"LBank Raw response text: {response.text[:500]}")
                    return None
            else:
                error_text = response.text[:200]
                self.last_error = f"HTTP {response.status_code}: {error_text}"
                logging.error(f"LBank public request error {response.status_code} for {endpoint}: {error_text}")
                logging.error(f"LBank Full error response: {response.text}")
                return None
                
        except Exception as e:
            self.last_error = f"Public request failed: {str(e)}"
            logging.error(f"LBank public request exception for {endpoint}: {e}")
            logging.error(f"LBank Request details - URL: {url}, Params: {params}")
            return None
    
    # Core API Methods - Rewritten from scratch per LBank documentation
    
    # Add missing method that's referenced by other methods
    def _signed_request(self, method: str, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Alias for _make_signed_request for backward compatibility"""
        return self._make_signed_request(endpoint, params)
    
    def get_ticker(self, symbol: str) -> Optional[Dict]:
        """Get ticker information for symbol"""
        try:
            lbank_symbol = self.convert_to_lbank_symbol(symbol)
            result = self._make_public_request("/v2/supplement/ticker/price.do", {
                'symbol': lbank_symbol
            })
            return result
        except Exception as e:
            logging.error(f"LBank get_ticker failed: {e}")
            return None
    
    def _public_request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make public (unsigned) request to LBank"""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.get(url, params=params, timeout=10)
            if response.status_code == 200:
                result = response.json()
                # Return the full response to maintain consistency
                return result
            return None
        except Exception as e:
            logging.error(f"LBank public request failed: {e}")
            return None
    
    # Remove duplicate method - using the new one defined above
    
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
    
    def get_api_restrictions(self) -> Optional[Dict]:
        """Get API restrictions using LBank v2 supplement API
        
        Official endpoint: POST /v2/supplement/api_Restrictions.do
        Returns API key permissions and restrictions
        """
        try:
            result = self._make_signed_request("/v2/supplement/api_Restrictions.do", {})
            
            if isinstance(result, dict) and result.get('result') == 'true' and 'data' in result:
                logging.info(f"LBank API restrictions fetched successfully")
                return result['data']  # Return the data part, not the full result
            else:
                error_msg = result.get('error_code', 'API restrictions fetch failed') if isinstance(result, dict) and result else 'No response'
                logging.warning(f"LBank API restrictions fetch failed: {error_msg}")
                return None
                
        except Exception as e:
            logging.error(f"LBank get_api_restrictions error: {e}")
            self.last_error = f"API Restrictions Error: {str(e)}"
            return None
    
    # Account Methods - Perpetual Futures Only
    # Note: Spot balance functions using supplement/user_info.do have been removed
    
    
    def get_futures_balance(self) -> List[Dict]:
        """
        Get perpetual futures account balance using LBank Perpetual Futures API
        
        Official endpoint: POST /cfd/openApi/v1/prv/account
        Returns perpetual futures account info with margin and available balances
        """
        try:
            # Use perpetual futures account endpoint - try GET method first
            result = self._make_signed_request(f"{self.private_path}/account", {
                'asset': 'USDT',         # Required parameter according to LBank docs
                'productGroup': 'SwapU'  # USDT-margined perpetual contracts
            }, method='GET')
            
            logging.debug(f"LBank perpetual futures balance result: {result}")
            
            if isinstance(result, dict) and (result.get('result') == True or result.get('result') == 'true'):
                balances = []
                
                # Handle perpetual futures balance response format
                if 'data' in result:
                    data = result['data']
                    
                    # Handle different response formats
                    if isinstance(data, list):
                        # List of assets
                        for asset_data in data:
                            if isinstance(asset_data, dict):
                                asset_code = asset_data.get('asset', 'USDT').upper()
                                balances.append({
                                    'asset': asset_code,
                                    'balance': str(asset_data.get('balance', 0)),
                                    'availableBalance': str(asset_data.get('availableBalance', 0)),
                                    'positionMargin': str(asset_data.get('positionMargin', 0)),
                                    'orderMargin': str(asset_data.get('orderMargin', 0)),
                                    'crossUnRealizedPnl': str(asset_data.get('crossUnRealizedPnl', 0))
                                })
                    elif isinstance(data, dict):
                        # Single account data
                        balances.append({
                            'asset': 'USDT',
                            'balance': str(data.get('balance', 0)),
                            'availableBalance': str(data.get('availableBalance', 0)),
                            'positionMargin': str(data.get('positionMargin', 0)),
                            'orderMargin': str(data.get('orderMargin', 0)),
                            'crossUnRealizedPnl': str(data.get('crossUnRealizedPnl', 0))
                        })
                            
                logging.info(f"LBank perpetual futures balance: Found {len(balances)} assets")
                return balances
            else:
                error_msg = result.get('error_code', 'Account balance fetch failed') if isinstance(result, dict) and result else 'No response'
                logging.warning(f"LBank perpetual futures balance failed: {error_msg}")
                return []
                
        except Exception as e:
            import traceback
            logging.error(f"LBank get_futures_balance error: {e}")
            logging.error(f"Full traceback: {traceback.format_exc()}")
            self.last_error = f"Perpetual Futures Balance Error: {str(e)}"
            return []

    def get_margin_balance(self) -> List[Dict]:
        """
        Get margin account balance - alias for futures balance in perpetual contracts
        
        For perpetual futures, margin and futures balance are the same
        """
        return self.get_futures_balance()
    
    def set_leverage(self, symbol: str, leverage: int, margin_type: str = 'cross') -> Dict:
        """
        Set leverage for a perpetual futures symbol
        
        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT')
            leverage: Leverage multiplier (1-200)
            margin_type: 'cross' or 'isolated'
        
        Returns:
            Dict with success status and details
        """
        try:
            # Convert symbol to LBank format
            lbank_symbol = self.convert_to_lbank_symbol(symbol)
            
            params = {
                'symbol': lbank_symbol,
                'leverage': leverage,
                'marginType': margin_type,
                'productGroup': 'SwapU'
            }
            
            result = self._make_signed_request(f"{self.private_path}/leverage", params)
            
            if isinstance(result, dict) and (result.get('result') == True or result.get('result') == 'true'):
                logging.info(f"LBank leverage set successfully: {symbol} to {leverage}x {margin_type}")
                return {
                    'success': True,
                    'symbol': symbol,
                    'leverage': leverage,
                    'margin_type': margin_type,
                    'message': 'Leverage updated successfully'
                }
            else:
                error_msg = result.get('error_code', 'Failed to set leverage') if isinstance(result, dict) else 'No response'
                logging.error(f"LBank set leverage failed: {error_msg}")
                self.last_error = f"Set Leverage Error: {error_msg}"
                return {
                    'success': False,
                    'error': error_msg
                }
                
        except Exception as e:
            logging.error(f"LBank set_leverage error: {e}")
            self.last_error = f"Set Leverage Error: {str(e)}"
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_leverage(self, symbol: str) -> Dict:
        """
        Get current leverage for a perpetual futures symbol
        
        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT')
        
        Returns:
            Dict with leverage information
        """
        try:
            # Convert symbol to LBank format
            lbank_symbol = self.convert_to_lbank_symbol(symbol)
            
            params = {
                'symbol': lbank_symbol,
                'productGroup': 'SwapU'
            }
            
            result = self._make_signed_request(f"{self.private_path}/leverageInfo", params)
            
            if isinstance(result, dict) and (result.get('result') == True or result.get('result') == 'true'):
                data = result.get('data', {})
                return {
                    'success': True,
                    'symbol': symbol,
                    'leverage': data.get('leverage', 20),  # Default 20x
                    'margin_type': data.get('marginType', 'cross'),
                    'max_leverage': data.get('maxLeverage', 200)
                }
            else:
                error_msg = result.get('error_code', 'Failed to get leverage') if isinstance(result, dict) else 'No response'
                logging.warning(f"LBank get leverage failed: {error_msg}")
                self.last_error = f"Get Leverage Error: {error_msg}"
                return {
                    'success': False,
                    'error': error_msg
                }
                
        except Exception as e:
            logging.error(f"LBank get_leverage error: {e}")
            self.last_error = f"Get Leverage Error: {str(e)}"
            return {
                'success': False,
                'error': str(e)
            }

    def get_positions(self) -> List[Dict]:
        """
        Get all open perpetual futures positions
        
        Returns list of position dictionaries with position information
        """
        try:
            params = {
                'productGroup': 'SwapU'  # USDT-margined perpetual contracts
            }
            
            result = self._make_signed_request(f"{self.private_path}/positions", params)
            
            if isinstance(result, dict) and (result.get('result') == True or result.get('result') == 'true'):
                if 'data' in result:
                    positions = result['data']
                    if isinstance(positions, list):
                        # Convert to standard format
                        standardized_positions = []
                        for pos in positions:
                            if isinstance(pos, dict):
                                standardized_positions.append({
                                    'symbol': self.convert_from_lbank_symbol(pos.get('symbol', '')),
                                    'side': pos.get('side', ''),
                                    'size': pos.get('size', 0),
                                    'notional': pos.get('notional', 0),
                                    'markPrice': pos.get('markPrice', 0),
                                    'entryPrice': pos.get('entryPrice', 0),
                                    'unrealizedPnl': pos.get('unrealizedPnl', 0),
                                    'percentage': pos.get('percentage', 0),
                                    'marginRatio': pos.get('marginRatio', 0),
                                    'leverage': pos.get('leverage', 20)
                                })
                        return standardized_positions
                    elif isinstance(positions, dict):
                        # Single position
                        return [{
                            'symbol': self.convert_from_lbank_symbol(positions.get('symbol', '')),
                            'side': positions.get('side', ''),
                            'size': positions.get('size', 0),
                            'notional': positions.get('notional', 0),
                            'markPrice': positions.get('markPrice', 0),
                            'entryPrice': positions.get('entryPrice', 0),
                            'unrealizedPnl': positions.get('unrealizedPnl', 0),
                            'percentage': positions.get('percentage', 0),
                            'marginRatio': positions.get('marginRatio', 0),
                            'leverage': positions.get('leverage', 20)
                        }]
                return []
            else:
                logging.warning(f"LBank get positions failed: {result.get('error_code', 'Unknown error') if isinstance(result, dict) else 'No response'}")
                return []
                
        except Exception as e:
            logging.error(f"LBank get_positions error: {e}")
            self.last_error = f"Get Positions Error: {str(e)}"
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
        Place a new order following LBank v1 API specifications
        
        Official endpoint: POST /v1/create_order.do
        Parameters:
        - symbol: Trading pair (e.g., btc_usdt)
        - type: buy/sell
        - amount: Order quantity
        - price: Order price (required for limit orders)
        """
        try:
            lbank_symbol = self.convert_to_lbank_symbol(symbol)
            
            # Prepare order parameters according to LBank format
            params = {
                'symbol': lbank_symbol,
                'type': side.lower(),  # buy or sell
                'amount': str(quantity)
            }
            
            # Price is required for limit orders
            if order_type.upper() == 'LIMIT':
                if not price:
                    raise ValueError("Price is required for limit orders")
                params['price'] = str(price)
            
            # Add required perpetual futures parameters
            params.update({
                'productGroup': 'SwapU',  # USDT-margined perpetual contracts
                'positionSide': side.upper()  # LONG or SHORT for perpetual futures
            })
            
            # Place order using perpetual futures API - try multiple endpoints for compatibility
            result = self._make_signed_request(f"{self.private_path}/submitOrder", params)
            
            # If that fails, try alternative endpoint
            if not result or (isinstance(result, dict) and result.get('error_code') == 405):
                logging.warning("LBank primary order endpoint failed, trying alternative...")
                result = self._make_signed_request(f"{self.private_path}/createOrder", params)
            
            if result and (result.get('result') == True or result.get('result') == 'true'):
                data = result.get('data', {})
                order_id = data.get('orderId') or data.get('order_id', '')
                
                if order_id:
                    logging.info(f"LBank perpetual futures order placed successfully: {order_id}")
                    
                    return {
                        'orderId': str(order_id),
                        'symbol': symbol,
                        'side': side.upper(),
                        'type': order_type.upper(),
                        'quantity': quantity,
                        'price': price,
                        'status': 'NEW',
                        'productGroup': 'SwapU'
                    }
            
            error_msg = result.get('error_code', 'Order placement failed') if result else 'No response'
            error_detail = result.get('msg', '') if result else ''
            full_error = f"{error_msg}: {error_detail}" if error_detail else error_msg
            
            logging.error(f"LBank perpetual futures order failed: {full_error}")
            self.last_error = f"Order Error: {full_error}"
            return None
            
        except Exception as e:
            logging.error(f"LBank place_order exception: {e}")
            self.last_error = f"Order placement failed: {str(e)}"
            return None
    
    def cancel_order(self, symbol: str, order_id: str) -> Optional[Dict]:
        """Cancel an existing perpetual futures order"""
        try:
            lbank_symbol = self.convert_to_lbank_symbol(symbol)
            params = {
                'symbol': lbank_symbol,
                'orderId': order_id,
                'productGroup': 'SwapU'
            }
            
            result = self._make_signed_request(f"{self.private_path}/cancelOrder", params)
            
            if result and (result.get('result') == True or result.get('result') == 'true'):
                logging.info(f"LBank perpetual futures order cancelled successfully: {order_id}")
                return result
            else:
                error_msg = result.get('error_code', 'Cancel order failed') if result else 'No response'
                logging.error(f"LBank cancel order failed: {error_msg}")
                self.last_error = f"Cancel Order Error: {error_msg}"
                return None
            
        except Exception as e:
            logging.error(f"LBank cancel_order failed: {e}")
            self.last_error = f"Cancel Order Error: {str(e)}"
            return None
    
    def get_order(self, symbol: str, order_id: str) -> Optional[Dict]:
        """Get perpetual futures order status"""
        try:
            lbank_symbol = self.convert_to_lbank_symbol(symbol)
            params = {
                'symbol': lbank_symbol,
                'orderId': order_id,
                'productGroup': 'SwapU'
            }
            
            result = self._make_signed_request(f"{self.private_path}/orderInfo", params)
            
            if result and (result.get('result') == True or result.get('result') == 'true'):
                return result
            else:
                error_msg = result.get('error_code', 'Get order failed') if result else 'No response'
                logging.warning(f"LBank get order failed: {error_msg}")
                self.last_error = f"Get Order Error: {error_msg}"
                return None
            
        except Exception as e:
            logging.error(f"LBank get_order failed: {e}")
            self.last_error = f"Get Order Error: {str(e)}"
            return None
    
    def get_order_history(self, symbol: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """Get order history"""
        try:
            # LBank requires symbol, return empty if not provided
            if not symbol:
                return []
                
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
        """Change leverage for perpetual futures trading using new API"""
        try:
            result = self.set_leverage(symbol, leverage)
            
            if result and result.get('success'):
                return {
                    'result': 'true',
                    'message': f'Leverage set to {leverage}x for {symbol}',
                    'symbol': symbol,
                    'leverage': leverage
                }
            else:
                error_msg = result.get('error', 'Failed to set leverage') if result else 'Unknown error'
                return {
                    'result': 'false',
                    'message': f'Failed to set leverage: {error_msg}',
                    'symbol': symbol,
                    'leverage': leverage,
                    'error': error_msg
                }
            
        except Exception as e:
            logging.error(f"LBank change_leverage failed: {e}")
            return {
                'result': 'false',
                'message': f'Failed to set leverage: {str(e)}',
                'symbol': symbol,
                'leverage': leverage,
                'error': str(e)
            }
    
    def change_margin_type(self, symbol: str, margin_type: str = "cross") -> Optional[Dict]:
        """Change margin type for perpetual futures (cross/isolated)"""
        try:
            lbank_symbol = self.convert_to_lbank_symbol(symbol)
            params = {
                'symbol': lbank_symbol,
                'marginType': margin_type.lower(),  # 'cross' or 'isolated'
                'productGroup': 'SwapU'
            }
            
            result = self._make_signed_request(f"{self.private_path}/marginType", params)
            
            if result and (result.get('result') == True or result.get('result') == 'true'):
                logging.info(f"LBank margin type changed successfully: {symbol} to {margin_type}")
                return result
            else:
                error_msg = result.get('error_code', 'Failed to change margin type') if result else 'No response'
                logging.error(f"LBank change margin type failed: {error_msg}")
                self.last_error = f"Change Margin Type Error: {error_msg}"
                return None
            
        except Exception as e:
            logging.error(f"LBank change_margin_type failed: {e}")
            self.last_error = f"Change Margin Type Error: {str(e)}"
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
    
    # Utility Methods
    def test_connectivity(self) -> bool:
        """Test API connectivity and authentication"""
        try:
            # Test public endpoint first  
            result = self._public_request("/v2/timestamp.do")
            if result:
                logging.debug("LBank public API accessible")
                
                # Test authenticated endpoint with debug info
                logging.debug("Testing LBank authentication...")
                balance_result = self.get_futures_balance()
                
                if balance_result is not None:
                    logging.debug("LBank authentication successful")
                    return True
                else:
                    logging.warning(f"LBank authentication failed: {self.last_error}")
                    return False
            return False
        except Exception as e:
            logging.error(f"LBank connectivity test failed: {e}")
            return False
    
    def get_last_error(self) -> Optional[str]:
        """Get the last error message"""
        return self.last_error
    
    # Compatibility methods to match ToobitClient interface
    def convert_to_toobit_symbol(self, symbol: str) -> str:
        """Convert to Toobit symbol format - compatibility method for LBank"""
        # For LBank, we just return the LBank format since each client handles its own format
        return self.convert_to_lbank_symbol(symbol)
    
    def place_multiple_tp_sl_orders(self, symbol: str, side: str, total_quantity: str, 
                                   take_profits: List[Dict], stop_loss_price: Optional[str] = None) -> List[Dict]:
        """
        Place multiple TP/SL orders for LBank perpetual futures
        
        Args:
            symbol: Trading pair (e.g., 'BTCUSDT')
            side: Original position side ('BUY' for long, 'SELL' for short)  
            total_quantity: Total position size
            take_profits: List of TP configs with 'price' and 'allocation'
            stop_loss_price: Stop loss trigger price
            
        Returns:
            List of placed order dictionaries
        """
        orders_placed = []
        
        try:
            # Place take profit orders as LIMIT orders to close positions
            for i, tp in enumerate(take_profits):
                # For closing positions: opposite side to entry
                tp_side = "sell" if side.upper() == "BUY" else "buy"
                tp_quantity = float(total_quantity) * tp['allocation'] / 100
                
                # Use proper LBank position closing parameters
                tp_params = {
                    'symbol': self.convert_to_lbank_symbol(symbol),
                    'type': tp_side,  # buy/sell in lowercase
                    'amount': str(tp_quantity),
                    'price': str(tp['price']),
                    'productGroup': 'SwapU',
                    'positionSide': 'SHORT' if side.upper() == "BUY" else 'LONG'  # Closing position
                }
                
                # Try multiple endpoints for TP order placement
                tp_result = self._make_signed_request(f"{self.private_path}/submitOrder", tp_params)
                
                # If that fails, try alternative endpoint
                if not tp_result or (isinstance(tp_result, dict) and tp_result.get('error_code') == 405):
                    logging.warning("LBank primary TP order endpoint failed, trying alternative...")
                    tp_result = self._make_signed_request(f"{self.private_path}/createOrder", tp_params)
                
                if tp_result and (tp_result.get('result') == True or tp_result.get('result') == 'true'):
                    data = tp_result.get('data', {})
                    order_id = data.get('orderId') or data.get('order_id', '')
                    
                    if order_id:
                        tp_order = {
                            'orderId': str(order_id),
                            'symbol': symbol,
                            'side': tp_side.upper(),
                            'type': 'LIMIT',
                            'quantity': str(tp_quantity),
                            'price': str(tp['price']),
                            'status': 'NEW',
                            'orderType': 'TAKE_PROFIT'
                        }
                        orders_placed.append(tp_order)
                        logging.info(f"LBank TP{i+1} order placed: {order_id} at ${tp['price']} for {tp['allocation']}%")
            
            # Place stop loss order as STOP_MARKET for guaranteed execution
            if stop_loss_price:
                sl_side = "sell" if side.upper() == "BUY" else "buy"
                
                # LBank stop loss parameters for perpetual futures
                # Try STOP_MARKET first, fallback to regular order with trigger price
                sl_params = {
                    'symbol': self.convert_to_lbank_symbol(symbol),
                    'type': sl_side,  # buy/sell in lowercase
                    'amount': str(total_quantity),
                    'productGroup': 'SwapU',
                    'positionSide': 'SHORT' if side.upper() == "BUY" else 'LONG'  # Closing position
                }
                
                # Add stop price parameters - try different formats for compatibility
                sl_params['stopPrice'] = str(stop_loss_price)  # Primary trigger price
                sl_params['trigger_price'] = str(stop_loss_price)  # Alternative format
                sl_params['orderType'] = 'STOP_MARKET'  # Market execution when triggered
                sl_params['workingType'] = 'MARK_PRICE'  # Use mark price to avoid false triggers
                
                # Try multiple endpoints for SL order placement
                sl_result = self._make_signed_request(f"{self.private_path}/submitOrder", sl_params)
                
                # If that fails, try alternative endpoint
                if not sl_result or (isinstance(sl_result, dict) and sl_result.get('error_code') == 405):
                    logging.warning("LBank primary SL order endpoint failed, trying alternative...")
                    sl_result = self._make_signed_request(f"{self.private_path}/createOrder", sl_params)
                
                if sl_result and (sl_result.get('result') == True or sl_result.get('result') == 'true'):
                    data = sl_result.get('data', {})
                    order_id = data.get('orderId') or data.get('order_id', '')
                    
                    if order_id:
                        sl_order = {
                            'orderId': str(order_id),
                            'symbol': symbol,
                            'side': sl_side.upper(),
                            'type': 'STOP_MARKET',
                            'quantity': str(total_quantity),
                            'stopPrice': str(stop_loss_price),
                            'status': 'NEW',
                            'orderType': 'STOP_LOSS'
                        }
                        orders_placed.append(sl_order)
                        logging.info(f"LBank SL order placed: {order_id} at ${stop_loss_price} (STOP_MARKET)")
                else:
                    error_msg = sl_result.get('error_code', 'Stop loss order failed') if sl_result else 'No response'
                    logging.warning(f"LBank stop loss order failed: {error_msg}")
        
        except Exception as e:
            logging.error(f"Error placing LBank TP/SL orders: {e}")
            import traceback
            logging.error(f"Full traceback: {traceback.format_exc()}")
        
        logging.info(f"LBank TP/SL orders completed: {len(orders_placed)} orders placed")
        return orders_placed
    
    # Additional compatibility methods to match ToobitClient interface
    def test_connection(self):
        """Test connection and return status with message - used by exchange sync scripts"""
        try:
            if self.test_connectivity():
                return True, "Connection successful"
            else:
                return False, "Connection failed - ping request failed"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
    
    def get_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        """Get orders - alias for get_order_history used by exchange sync scripts"""
        if symbol:
            return self.get_order_history(symbol)
        else:
            # LBank doesn't have a generic get all orders method, return empty list
            return []


class ExchangeClientFactory:
    """Factory for creating exchange clients based on user preferences"""
    
    @staticmethod
    def create_client(exchange_name: str, api_key: str, api_secret: str, 
                     passphrase: str = "", testnet: bool = False):
        """
        Create appropriate exchange client based on exchange name
        
        Args:
            exchange_name: Name of the exchange ('toobit', 'lbank')
            api_key: API key for authentication
            api_secret: API secret for authentication
            passphrase: Passphrase (if required by exchange)
            testnet: Use testnet mode (if supported by exchange)
            
        Returns:
            Exchange client instance (ToobitClient or LBankClient)
            
        Raises:
            ValueError: If exchange is not supported
        """
        exchange_name = exchange_name.lower().strip()
        
        if exchange_name == "toobit":
            logging.debug(f"Creating ToobitClient for exchange: {exchange_name}")
            return ToobitClient(
                api_key=api_key,
                api_secret=api_secret,
                passphrase=passphrase,
                testnet=testnet  # Toobit will automatically use mainnet regardless
            )
        
        elif exchange_name == "lbank":
            logging.debug(f"Creating LBankClient for exchange: {exchange_name}")
            return LBankClient(
                api_key=api_key,
                api_secret=api_secret,
                passphrase=passphrase,
                testnet=testnet  # LBank supports testnet
            )
        
        else:
            supported_exchanges = ", ".join(TradingConfig.SUPPORTED_EXCHANGES)
            error_msg = f"Unsupported exchange: {exchange_name}. Supported exchanges: {supported_exchanges}"
            logging.error(error_msg)
            raise ValueError(error_msg)
    
    @staticmethod
    def get_supported_exchanges():
        """Get list of supported exchanges"""
        return TradingConfig.SUPPORTED_EXCHANGES
    
    @staticmethod
    def is_exchange_supported(exchange_name: str) -> bool:
        """Check if an exchange is supported"""
        return exchange_name.lower().strip() in TradingConfig.SUPPORTED_EXCHANGES


def create_exchange_client(user_credentials, testnet: bool = False):
    """
    Convenience function to create exchange client from UserCredentials object
    
    Args:
        user_credentials: UserCredentials database object
        testnet: Override testnet mode (optional)
        
    Returns:
        Exchange client instance
    """
    if not user_credentials or not user_credentials.has_credentials():
        raise ValueError("Invalid or incomplete user credentials")
    
    # Use provided testnet mode or fall back to user's preference
    use_testnet = testnet if testnet is not False else (user_credentials.testnet_mode if user_credentials else False)
    
    # Get exchange name from user credentials, default to toobit
    exchange_name = user_credentials.exchange_name or TradingConfig.DEFAULT_EXCHANGE
    
    try:
        return ExchangeClientFactory.create_client(
            exchange_name=exchange_name,
            api_key=user_credentials.get_api_key(),
            api_secret=user_credentials.get_api_secret(),
            passphrase=user_credentials.get_passphrase() or "",
            testnet=use_testnet
        )
    except Exception as e:
        logging.error(f"Failed to create exchange client for {exchange_name}: {e}")
        raise


class ExchangeClientWrapper:
    """
    Wrapper that provides a unified interface for different exchange clients
    This ensures all exchange clients have consistent method signatures
    """
    
    def __init__(self, client, exchange_name: str):
        self.client = client
        self.exchange_name = exchange_name.lower()
        
    def __getattr__(self, name):
        """Delegate all method calls to the underlying client"""
        return getattr(self.client, name)
    
    def get_exchange_name(self) -> str:
        """Get the name of the exchange"""
        return self.exchange_name
    
    def get_last_error(self) -> Optional[str]:
        """Get the last error message from the client"""
        return getattr(self.client, 'last_error', None)
    
    def is_testnet(self) -> bool:
        """Check if the client is in testnet mode"""
        return getattr(self.client, 'testnet', False)


def create_wrapped_exchange_client(user_credentials=None, exchange_name: str = "toobit", testnet: bool = False):
    """
    Create a wrapped exchange client that provides additional functionality
    Can work with or without user credentials for anonymous access
    
    Args:
        user_credentials: UserCredentials database object (optional)
        exchange_name: Name of exchange to use (fallback if no credentials)
        testnet: Override testnet mode (optional)
        
    Returns:
        ExchangeClientWrapper instance
    """
    if user_credentials and user_credentials.has_credentials():
        client = create_exchange_client(user_credentials, testnet)
        actual_exchange_name = user_credentials.exchange_name or TradingConfig.DEFAULT_EXCHANGE
    else:
        # Create anonymous client for public data access
        client = ExchangeClientFactory.create_client(
            exchange_name=exchange_name,
            api_key="",
            api_secret="",
            passphrase="",
            testnet=testnet
        )
        actual_exchange_name = exchange_name
    
    return ExchangeClientWrapper(client, actual_exchange_name)