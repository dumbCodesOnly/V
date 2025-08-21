"""
Toobit Exchange API Client for USDT-M Futures Trading
Handles authentication, order management, and position synchronization
"""

import hashlib
import hmac
import time
import requests
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

class ToobitClient:
    """Toobit Exchange API Client for futures trading"""
    
    def __init__(self, api_key: str, api_secret: str, passphrase: str = "", testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.testnet = testnet
        
        # Base URLs for Toobit API - Fixed URLs based on official documentation
        # Note: Toobit may not have separate testnet URL, using main API with testnet credentials
        self.base_url = "https://api.toobit.com"
        self.futures_base = "/api/v1/futures"
        
        # Request session for connection pooling
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/x-www-form-urlencoded',  # Toobit expects form-encoded data
            'User-Agent': 'TradingExpert/1.0'
        })
        
    def _generate_signature(self, params_string: str) -> str:
        """Generate signature for Toobit API authentication"""
        # Toobit uses HMAC SHA256 on the parameter string only
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            params_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def _make_request(self, method: str, endpoint: str, params: Optional[Dict] = None, data: Optional[Dict] = None, authenticated: bool = True) -> Dict:
        """Make authenticated request to Toobit API following official documentation format"""
        timestamp = str(int(time.time() * 1000))
        
        # Combine all parameters (query params + data + required params)
        all_params = {}
        if params:
            all_params.update(params)
        if data:
            all_params.update(data)
        
        # Only add authentication parameters for authenticated endpoints
        if authenticated:
            all_params['timestamp'] = timestamp
        
        # Create parameter string for signature (sorted by key)
        sorted_params = sorted(all_params.items())
        params_string = "&".join([f"{k}={v}" for k, v in sorted_params])
        
        # Set headers 
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        
        # Add authentication headers and signature for authenticated requests
        if authenticated:
            signature = self._generate_signature(params_string)
            all_params['signature'] = signature
            headers['X-BB-APIKEY'] = self.api_key
        
        url = self.base_url + self.futures_base + endpoint
        
        # Enhanced logging for debugging API calls
        api_mode = "TESTNET" if self.testnet else "LIVE"
        logging.info(f"[{api_mode}] Toobit API Call: {method} {url}")
        logging.info(f"[{api_mode}] Parameters: {all_params}")
        
        try:
            # For GET requests with parameters, use query string instead of form data
            if method.upper() == 'GET' and all_params:
                response = self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=all_params,  # Query parameters for GET
                    timeout=30
                )
            else:
                # For POST requests, use form-encoded data 
                response = self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=all_params if all_params else None,  # Form-encoded data
                    timeout=30
                )
            
            logging.info(f"[{api_mode}] Response status: {response.status_code}")
            response.raise_for_status()
            result = response.json()
            logging.info(f"[{api_mode}] Response: {result}")
            return result
            
        except requests.exceptions.RequestException as e:
            logging.error(f"[{api_mode}] Toobit API request failed: {e}")
            logging.error(f"[{api_mode}] Request details: {method} {url}")
            logging.error(f"[{api_mode}] Request headers: {headers}")
            logging.error(f"[{api_mode}] Request params: {all_params}")
            response = getattr(e, 'response', None)
            if response is not None:
                logging.error(f"[{api_mode}] Response body: {response.text}")
                # Try to decode error response for more details
                try:
                    error_data = response.json()
                    logging.error(f"[{api_mode}] Error details: {error_data}")
                except:
                    pass
            raise
        except json.JSONDecodeError as e:
            logging.error(f"[{api_mode}] Toobit API response decode failed: {e}")
            if 'response' in locals():
                logging.error(f"[{api_mode}] Raw response: {response.text}")
            else:
                logging.error(f"[{api_mode}] No response object available")
            raise
    
    def get_account_balance(self) -> Dict:
        """Get futures account balance"""
        try:
            # Balance endpoint might not need additional parameters
            response = self._make_request('GET', '/balance', params={})
            return response if response else {}
        except Exception as e:
            logging.error(f"Failed to get account balance: {e}")
            return {}
    
    def get_positions(self) -> List[Dict]:
        """Get all open positions"""
        try:
            response = self._make_request('GET', '/positions')
            return response.get('data', []) if response else []
        except Exception as e:
            logging.error(f"Failed to get positions: {e}")
            return []
    
    def get_position(self, symbol: str) -> Optional[Dict]:
        """Get specific position by symbol"""
        try:
            response = self._make_request('GET', f'/position/{symbol}')
            return response.get('data') if response else None
        except Exception as e:
            logging.error(f"Failed to get position for {symbol}: {e}")
            return None
    
    def get_orders(self, symbol: Optional[str] = None, status: Optional[str] = None) -> List[Dict]:
        """Get orders with optional filters"""
        try:
            params = {}
            if symbol:
                params['symbol'] = symbol
            if status:
                params['status'] = status
                
            response = self._make_request('GET', '/orders', params=params)
            return response.get('data', []) if response else []
        except Exception as e:
            logging.error(f"Failed to get orders: {e}")
            return []
    
    def get_order_status(self, order_id: str) -> Optional[Dict]:
        """Get specific order status"""
        try:
            response = self._make_request('GET', f'/order/{order_id}')
            return response.get('data') if response else None
        except Exception as e:
            logging.error(f"Failed to get order status for {order_id}: {e}")
            return None
    
    def place_order(self, symbol: str, side: str, order_type: str, quantity: str, 
                   price: Optional[str] = None, stop_price: Optional[str] = None, **kwargs) -> Optional[Dict]:
        """Place a new order using Toobit API format"""
        try:
            # Format order data according to Toobit documentation
            data = {
                'symbol': symbol.upper(),  # e.g., BTCUSDT
                'side': side.upper(),  # BUY, SELL (uppercase as per docs)
                'type': order_type.upper(),  # MARKET, LIMIT, STOP_MARKET, etc.
                'quantity': str(quantity)
            }
            
            # Only add timeInForce for limit orders (not for market orders)
            if order_type.upper() in ['LIMIT', 'STOP_LIMIT']:
                data['timeInForce'] = kwargs.get('timeInForce', 'GTC')
            
            # Only add price for limit orders
            if price and order_type.upper() in ['LIMIT', 'STOP_LIMIT']:
                data['price'] = str(price)
            if stop_price:
                data['stopPrice'] = str(stop_price)
                
            # Add additional parameters (reduceOnly, etc.)
            for key, value in kwargs.items():
                if key not in ['leverage', 'timeInForce']:  # Skip these as they're handled above
                    data[key] = value
            
            response = self._make_request('POST', '/order', data=data)
            
            # Toobit might return different response format
            if response:
                return response
            return None
        except Exception as e:
            logging.error(f"Failed to place order: {e}")
            return None
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        try:
            response = self._make_request('DELETE', f'/order/{order_id}')
            return response.get('success', False) if response else False
        except Exception as e:
            logging.error(f"Failed to cancel order {order_id}: {e}")
            return False
    
    def place_tp_sl_orders(self, symbol: str, side: str, quantity: str, 
                          take_profit_price: Optional[str] = None, stop_loss_price: Optional[str] = None) -> List[Dict]:
        """Place take profit and stop loss orders"""
        orders_placed = []
        
        try:
            # Place take profit order (opposite side)
            if take_profit_price:
                tp_side = "sell" if side.lower() == "buy" else "buy"
                tp_order = self.place_order(
                    symbol=symbol,
                    side=tp_side,
                    order_type="limit",
                    quantity=quantity,
                    price=take_profit_price,
                    timeInForce="GTC",
                    reduceOnly=True
                )
                if tp_order:
                    orders_placed.append({"type": "take_profit", "order": tp_order})
            
            # Place stop loss order (opposite side)
            if stop_loss_price:
                sl_side = "sell" if side.lower() == "buy" else "buy"
                sl_order = self.place_order(
                    symbol=symbol,
                    side=sl_side,
                    order_type="stop_market",
                    quantity=quantity,
                    stop_price=stop_loss_price,
                    timeInForce="GTC",
                    reduceOnly=True
                )
                if sl_order:
                    orders_placed.append({"type": "stop_loss", "order": sl_order})
                    
            return orders_placed
        except Exception as e:
            logging.error(f"Failed to place TP/SL orders: {e}")
            return orders_placed

    def get_ticker_price(self, symbol: str) -> Optional[float]:
        """Get current ticker price from Toobit exchange"""
        try:
            # Use public endpoint (no authentication required)
            response = self._make_request('GET', '/ticker/price', params={'symbol': symbol}, authenticated=False)
            
            if response and 'price' in response:
                return float(response['price'])
            elif response and isinstance(response, list) and len(response) > 0:
                # Some exchanges return array format
                return float(response[0].get('price', 0))
            return None
        except Exception as e:
            logging.error(f"Failed to get ticker price for {symbol} from Toobit: {e}")
            return None
    
    def get_market_data(self, symbol: str) -> Optional[Dict]:
        """Get comprehensive market data from Toobit"""
        try:
            # Try multiple possible endpoints for market data
            endpoints_to_try = [
                f'/ticker/24hr?symbol={symbol}',
                f'/ticker?symbol={symbol}',
                f'/depth?symbol={symbol}&limit=1'
            ]
            
            for endpoint in endpoints_to_try:
                try:
                    response = self._make_request('GET', endpoint, authenticated=False)
                    if response:
                        return response
                except:
                    continue
            
            return None
        except Exception as e:
            logging.error(f"Failed to get market data for {symbol} from Toobit: {e}")
            return None

    def place_multiple_tp_sl_orders(self, symbol: str, side: str, total_quantity: str,
                                   take_profits: Optional[List[Dict]] = None, stop_loss_price: Optional[str] = None) -> List[Dict]:
        """Place multiple partial take profit orders and one stop loss order"""
        orders_placed = []
        
        try:
            # Place multiple take profit orders (opposite side)
            if take_profits:
                tp_side = "sell" if side.lower() == "buy" else "buy"
                
                for i, tp in enumerate(take_profits):
                    tp_price = str(tp.get('price', 0))
                    tp_quantity = str(tp.get('quantity', 0))
                    
                    if float(tp_price) > 0 and float(tp_quantity) > 0:
                        tp_order = self.place_order(
                            symbol=symbol,
                            side=tp_side,
                            order_type="limit",
                            quantity=tp_quantity,
                            price=tp_price,
                            timeInForce="GTC",
                            reduceOnly=True
                        )
                        if tp_order:
                            orders_placed.append({
                                "type": f"take_profit_{i+1}", 
                                "order": tp_order,
                                "percentage": tp.get('percentage', 0),
                                "allocation": tp.get('allocation', 100)
                            })
            
            # Place stop loss order (opposite side, full remaining quantity)
            if stop_loss_price:
                sl_side = "sell" if side.lower() == "buy" else "buy"
                sl_order = self.place_order(
                    symbol=symbol,
                    side=sl_side,
                    order_type="stop_market",
                    quantity=total_quantity,
                    stop_price=stop_loss_price,
                    timeInForce="GTC",
                    reduceOnly=True
                )
                if sl_order:
                    orders_placed.append({"type": "stop_loss", "order": sl_order})
                    
            return orders_placed
        except Exception as e:
            logging.error(f"Failed to place multiple TP/SL orders: {e}")
            return orders_placed
    
    def get_trade_history(self, symbol: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Get trade history"""
        try:
            params = {'limit': str(limit)}
            if symbol:
                params['symbol'] = symbol
                
            response = self._make_request('GET', '/trades', params=params)
            return response.get('data', []) if response else []
        except Exception as e:
            logging.error(f"Failed to get trade history: {e}")
            return []
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test API connection and credentials"""
        try:
            response = self._make_request('GET', '/balance')
            if response:
                return True, "Connection successful"
            else:
                return False, "Invalid response from exchange"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"