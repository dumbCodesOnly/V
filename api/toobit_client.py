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
        
        # Base URLs for Toobit API
        self.base_url = "https://openapi.toobit.com" if not testnet else "https://sandbox-openapi.toobit.com"
        self.futures_base = "/api/v1/futures"
        
        # Request session for connection pooling
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'TradingExpert/1.0'
        })
        
    def _generate_signature(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        """Generate signature for Toobit API authentication"""
        message = timestamp + method.upper() + request_path + body
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def _make_request(self, method: str, endpoint: str, params: Optional[Dict] = None, data: Optional[Dict] = None) -> Dict:
        """Make authenticated request to Toobit API"""
        timestamp = str(int(time.time() * 1000))
        request_path = self.futures_base + endpoint
        
        # Prepare query string
        query_string = ""
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            request_path += "?" + query_string
        
        # Prepare body
        body = ""
        if data:
            body = json.dumps(data, separators=(',', ':'))
        
        # Generate signature
        signature = self._generate_signature(timestamp, method, request_path, body)
        
        # Set headers
        headers = {
            'TB-ACCESS-KEY': self.api_key,
            'TB-ACCESS-SIGN': signature,
            'TB-ACCESS-TIMESTAMP': timestamp,
            'TB-ACCESS-PASSPHRASE': self.passphrase if self.passphrase else "",
        }
        
        url = self.base_url + request_path
        
        try:
            response = self.session.request(
                method=method,
                url=url,
                headers=headers,
                json=data if data else None,
                params=params if params and not data else None,
                timeout=30
            )
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logging.error(f"Toobit API request failed: {e}")
            raise
        except json.JSONDecodeError as e:
            logging.error(f"Toobit API response decode failed: {e}")
            raise
    
    def get_account_balance(self) -> Dict:
        """Get futures account balance"""
        try:
            response = self._make_request('GET', '/account')
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
        """Place a new order"""
        try:
            data = {
                'symbol': symbol,
                'side': side.lower(),  # buy, sell
                'type': order_type.lower(),  # market, limit, stop, stop_limit
                'quantity': str(quantity)
            }
            
            if price:
                data['price'] = str(price)
            if stop_price:
                data['stopPrice'] = str(stop_price)
                
            # Add additional parameters
            data.update(kwargs)
            
            response = self._make_request('POST', '/order', data=data)
            return response.get('data') if response else None
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
            response = self._make_request('GET', '/account')
            if response:
                return True, "Connection successful"
            else:
                return False, "Invalid response from exchange"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"