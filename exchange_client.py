import os
import logging
import time
import hmac
import hashlib
import requests
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
import asyncio
import aiohttp

logger = logging.getLogger(__name__)


class ExchangeClient:
    """Toobit Exchange API Client for futures trading"""
    
    def __init__(self, testnet: bool = True) -> None:
        self.testnet = testnet
        
        # API endpoints
        if testnet:
            self.base_url = "https://testnet-api.toobit.com"
        else:
            self.base_url = "https://api.toobit.com"
        
        # API credentials from environment
        self.api_key = os.getenv("TOOBIT_API_KEY", "")
        self.api_secret = os.getenv("TOOBIT_API_SECRET", "")
        
        # Session for connection pooling
        self.session = None
        
        # Rate limiting
        self.last_request_time = 0
        self.min_request_interval = 0.1  # 100ms between requests
        
        if not self.api_key or not self.api_secret:
            logger.warning("Toobit API credentials not found. Trading will be simulated.")
            self.simulation_mode = True
        else:
            self.simulation_mode = False
    
    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()
    
    def _generate_signature(self, query_string: str) -> str:
        """Generate HMAC SHA256 signature for API requests"""
        return hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
    
    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds"""
        return int(time.time() * 1000)
    
    async def _rate_limit(self) -> None:
        """Implement rate limiting"""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        
        if time_since_last < self.min_request_interval:
            await asyncio.sleep(self.min_request_interval - time_since_last)
        
        self.last_request_time = time.time()
    
    async def _make_request(self, method: str, endpoint: str, params: Optional[Dict] = None, 
                           signed: bool = False) -> Optional[Dict[str, Any]]:
        """Make HTTP request to Toobit API"""
        if self.simulation_mode:
            logger.debug(f"Simulated {method} request to {endpoint}")
            return {"status": "simulated", "data": {}}
        
        try:
            await self._rate_limit()
            
            url = f"{self.base_url}{endpoint}"
            headers = {
                "X-CH-APIKEY": self.api_key,
                "Content-Type": "application/json"
            }
            
            if params is None:
                params = {}
            
            if signed:
                timestamp = self._get_timestamp()
                params['timestamp'] = timestamp
                
                # Create query string for signature
                query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
                signature = self._generate_signature(query_string)
                params['signature'] = signature
            
            if not self.session:
                self.session = aiohttp.ClientSession()
            
            if method.upper() == "GET":
                async with self.session.get(url, params=params, headers=headers) as response:
                    result = await response.json()
            elif method.upper() == "POST":
                async with self.session.post(url, json=params, headers=headers) as response:
                    result = await response.json()
            elif method.upper() == "PUT":
                async with self.session.put(url, json=params, headers=headers) as response:
                    result = await response.json()
            elif method.upper() == "DELETE":
                async with self.session.delete(url, params=params, headers=headers) as response:
                    result = await response.json()
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            if response.status != 200:
                logger.error(f"API request failed: {response.status} - {result}")
                return None
            
            return result
            
        except Exception as e:
            logger.error(f"Error making API request to {endpoint}: {e}")
            return None
    
    async def get_server_time(self) -> Optional[int]:
        """Get server time"""
        result = await self._make_request("GET", "/sapi/v1/time")
        return result.get("serverTime") if result else None
    
    async def get_exchange_info(self) -> Optional[Dict[str, Any]]:
        """Get exchange trading rules and symbol information"""
        return await self._make_request("GET", "/sapi/v1/exchangeInfo")
    
    async def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price for a symbol"""
        if self.simulation_mode:
            # Return simulated price based on symbol
            base_prices = {
                "BTCUSDT": 45000.0,
                "ETHUSDT": 3000.0,
                "BNBUSDT": 300.0,
                "ADAUSDT": 0.5,
                "SOLUSDT": 100.0,
                "XRPUSDT": 0.6,
                "DOTUSDT": 7.0,
                "DOGEUSDT": 0.08,
                "AVAXUSDT": 25.0,
                "LINKUSDT": 15.0,
                "MATICUSDT": 0.9,
                "UNIUSDT": 6.0
            }
            
            # Add some random variation (±2%)
            import random
            base_price = base_prices.get(symbol.replace("/", ""), 1.0)
            variation = random.uniform(-0.02, 0.02)
            return base_price * (1 + variation)
        
        try:
            result = await self._make_request("GET", "/sapi/v1/ticker/price", {"symbol": symbol.replace("/", "")})
            return float(result["price"]) if result and "price" in result else None
        except Exception as e:
            logger.error(f"Error getting price for {symbol}: {e}")
            return None
    
    async def get_ticker_24hr(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get 24hr ticker statistics"""
        params = {"symbol": symbol.replace("/", "")}
        return await self._make_request("GET", "/sapi/v1/ticker/24hr", params)
    
    async def get_order_book(self, symbol: str, limit: int = 100) -> Optional[Dict[str, Any]]:
        """Get order book for a symbol"""
        params = {"symbol": symbol.replace("/", ""), "limit": limit}
        return await self._make_request("GET", "/sapi/v1/depth", params)
    
    async def get_klines(self, symbol: str, interval: str, limit: int = 500) -> Optional[List[List]]:
        """Get kline/candlestick data"""
        params = {
            "symbol": symbol.replace("/", ""),
            "interval": interval,
            "limit": limit
        }
        result = await self._make_request("GET", "/sapi/v1/klines", params)
        return result if result else None
    
    async def get_account_info(self) -> Optional[Dict[str, Any]]:
        """Get account information"""
        return await self._make_request("GET", "/sapi/v1/account", signed=True)
    
    async def get_balance(self) -> Optional[Dict[str, float]]:
        """Get account balance"""
        if self.simulation_mode:
            return {
                "USDT": 10000.0,  # Simulated starting balance
                "totalWalletBalance": 10000.0,
                "totalUnrealizedProfit": 0.0,
                "totalMarginBalance": 10000.0,
                "availableBalance": 10000.0
            }
        
        account_info = await self.get_account_info()
        if not account_info:
            return None
        
        balances = {}
        if "balances" in account_info:
            for balance in account_info["balances"]:
                asset = balance["asset"]
                free = float(balance["free"])
                locked = float(balance["locked"])
                balances[asset] = free + locked
        
        return balances
    
    async def get_position_info(self, symbol: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """Get position information"""
        if self.simulation_mode:
            return []  # No positions in simulation mode
        
        params = {}
        if symbol:
            params["symbol"] = symbol.replace("/", "")
        
        return await self._make_request("GET", "/fapi/v2/positionRisk", params, signed=True)
    
    async def place_order(self, symbol: str, side: str, order_type: str, quantity: float,
                         price: Optional[float] = None, time_in_force: str = "GTC",
                         stop_price: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Place a new order"""
        if self.simulation_mode:
            # Return simulated order response
            import uuid
            return {
                "orderId": str(uuid.uuid4())[:8],
                "symbol": symbol.replace("/", ""),
                "status": "FILLED",
                "executedQty": str(quantity),
                "cummulativeQuoteQty": str(quantity * (price or 1.0)),
                "avgPrice": str(price or 1.0),
                "origQty": str(quantity),
                "side": side.upper(),
                "type": order_type.upper(),
                "timeInForce": time_in_force
            }
        
        params = {
            "symbol": symbol.replace("/", ""),
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": quantity,
            "timeInForce": time_in_force
        }
        
        if price:
            params["price"] = price
        
        if stop_price:
            params["stopPrice"] = stop_price
        
        return await self._make_request("POST", "/fapi/v1/order", params, signed=True)
    
    async def cancel_order(self, symbol: str, order_id: str) -> Optional[Dict[str, Any]]:
        """Cancel an existing order"""
        if self.simulation_mode:
            return {"orderId": order_id, "status": "CANCELLED"}
        
        params = {
            "symbol": symbol.replace("/", ""),
            "orderId": order_id
        }
        
        return await self._make_request("DELETE", "/fapi/v1/order", params, signed=True)
    
    async def get_order_status(self, symbol: str, order_id: str) -> Optional[Dict[str, Any]]:
        """Get order status"""
        if self.simulation_mode:
            return {"orderId": order_id, "status": "FILLED"}
        
        params = {
            "symbol": symbol.replace("/", ""),
            "orderId": order_id
        }
        
        return await self._make_request("GET", "/fapi/v1/order", params, signed=True)
    
    async def get_open_orders(self, symbol: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """Get all open orders"""
        if self.simulation_mode:
            return []
        
        params = {}
        if symbol:
            params["symbol"] = symbol.replace("/", "")
        
        return await self._make_request("GET", "/fapi/v1/openOrders", params, signed=True)
    
    async def get_trade_history(self, symbol: str, limit: int = 500) -> Optional[List[Dict[str, Any]]]:
        """Get trade history"""
        if self.simulation_mode:
            return []
        
        params = {
            "symbol": symbol.replace("/", ""),
            "limit": limit
        }
        
        return await self._make_request("GET", "/fapi/v1/userTrades", params, signed=True)
    
    async def set_leverage(self, symbol: str, leverage: int) -> Optional[Dict[str, Any]]:
        """Set leverage for a symbol"""
        if self.simulation_mode:
            return {"leverage": leverage, "symbol": symbol}
        
        params = {
            "symbol": symbol.replace("/", ""),
            "leverage": leverage
        }
        
        return await self._make_request("POST", "/fapi/v1/leverage", params, signed=True)
    
    async def change_margin_type(self, symbol: str, margin_type: str) -> Optional[Dict[str, Any]]:
        """Change margin type (ISOLATED or CROSSED)"""
        if self.simulation_mode:
            return {"marginType": margin_type}
        
        params = {
            "symbol": symbol.replace("/", ""),
            "marginType": margin_type.upper()
        }
        
        return await self._make_request("POST", "/fapi/v1/marginType", params, signed=True)
    
    # Trading bot specific methods
    
    async def check_entry_order_status(self, symbol: str, expected_price: Optional[float]) -> bool:
        """Check if entry order has been filled"""
        if self.simulation_mode:
            # Simulate order fill after some time
            import random
            return random.random() > 0.5  # 50% chance of being filled
        
        try:
            open_orders = await self.get_open_orders(symbol)
            if not open_orders:
                return True  # No open orders means it was filled
            
            # Check if any orders match our expected price
            for order in open_orders:
                if expected_price and abs(float(order.get("price", 0)) - expected_price) < 0.000001:
                    return False  # Order still open
            
            return True  # Order was filled or cancelled
            
        except Exception as e:
            logger.error(f"Error checking entry order status: {e}")
            return False
    
    async def close_position_partially(self, symbol: str, quantity: float, price: float) -> Optional[Dict[str, Any]]:
        """Close part of a position at take profit level"""
        if self.simulation_mode:
            logger.info(f"Simulated partial close: {quantity} {symbol} at {price}")
            return {"status": "filled", "quantity": quantity, "price": price}
        
        # Determine the opposite side for closing
        position_info = await self.get_position_info(symbol)
        if not position_info:
            return None
        
        position = next((p for p in position_info if p["symbol"] == symbol.replace("/", "")), None)
        if not position:
            return None
        
        position_side = position.get("positionSide", "BOTH")
        current_qty = float(position.get("positionAmt", 0))
        
        if current_qty == 0:
            return None
        
        # Determine close side
        close_side = "SELL" if current_qty > 0 else "BUY"
        
        return await self.place_order(
            symbol=symbol,
            side=close_side,
            order_type="LIMIT",
            quantity=abs(quantity),
            price=price
        )
    
    async def close_position_fully(self, symbol: str, price: float) -> Optional[Dict[str, Any]]:
        """Close entire position at stop loss"""
        if self.simulation_mode:
            logger.info(f"Simulated full close: {symbol} at {price}")
            return {"status": "filled", "price": price}
        
        position_info = await self.get_position_info(symbol)
        if not position_info:
            return None
        
        position = next((p for p in position_info if p["symbol"] == symbol.replace("/", "")), None)
        if not position:
            return None
        
        current_qty = float(position.get("positionAmt", 0))
        if current_qty == 0:
            return None
        
        close_side = "SELL" if current_qty > 0 else "BUY"
        
        return await self.place_order(
            symbol=symbol,
            side=close_side,
            order_type="MARKET",
            quantity=abs(current_qty)
        )
    
    async def update_stop_loss_order(self, symbol: str, new_stop_price: float) -> Optional[Dict[str, Any]]:
        """Update stop loss order price"""
        if self.simulation_mode:
            logger.info(f"Simulated SL update: {symbol} to {new_stop_price}")
            return {"status": "updated", "stopPrice": new_stop_price}
        
        try:
            # Cancel existing stop loss orders
            open_orders = await self.get_open_orders(symbol)
            for order in open_orders:
                if order.get("type") in ["STOP_MARKET", "STOP"]:
                    await self.cancel_order(symbol, order["orderId"])
            
            # Get current position to determine new stop loss side
            position_info = await self.get_position_info(symbol)
            if not position_info:
                return None
            
            position = next((p for p in position_info if p["symbol"] == symbol.replace("/", "")), None)
            if not position:
                return None
            
            current_qty = float(position.get("positionAmt", 0))
            if current_qty == 0:
                return None
            
            stop_side = "SELL" if current_qty > 0 else "BUY"
            
            # Place new stop loss order
            return await self.place_order(
                symbol=symbol,
                side=stop_side,
                order_type="STOP_MARKET",
                quantity=abs(current_qty),
                stop_price=new_stop_price
            )
            
        except Exception as e:
            logger.error(f"Error updating stop loss order: {e}")
            return None
    
    async def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get detailed symbol information including trading rules"""
        exchange_info = await self.get_exchange_info()
        if not exchange_info or "symbols" not in exchange_info:
            return None
        
        symbol_clean = symbol.replace("/", "")
        for sym in exchange_info["symbols"]:
            if sym["symbol"] == symbol_clean:
                return sym
        
        return None
    
    async def validate_order_params(self, symbol: str, quantity: float, price: Optional[float] = None) -> Tuple[bool, str]:
        """Validate order parameters against exchange rules"""
        if self.simulation_mode:
            return True, "Simulation mode - validation skipped"
        
        try:
            symbol_info = await self.get_symbol_info(symbol)
            if not symbol_info:
                return False, "Symbol not found"
            
            # Check if symbol is trading
            if symbol_info.get("status") != "TRADING":
                return False, f"Symbol {symbol} is not currently trading"
            
            # Validate filters
            for filter_info in symbol_info.get("filters", []):
                filter_type = filter_info.get("filterType")
                
                if filter_type == "LOT_SIZE":
                    min_qty = float(filter_info.get("minQty", 0))
                    max_qty = float(filter_info.get("maxQty", float('inf')))
                    step_size = float(filter_info.get("stepSize", 0))
                    
                    if quantity < min_qty:
                        return False, f"Quantity {quantity} below minimum {min_qty}"
                    if quantity > max_qty:
                        return False, f"Quantity {quantity} above maximum {max_qty}"
                    if step_size > 0 and (quantity % step_size) != 0:
                        return False, f"Quantity {quantity} not valid step size {step_size}"
                
                elif filter_type == "PRICE_FILTER" and price:
                    min_price = float(filter_info.get("minPrice", 0))
                    max_price = float(filter_info.get("maxPrice", float('inf')))
                    tick_size = float(filter_info.get("tickSize", 0))
                    
                    if price < min_price:
                        return False, f"Price {price} below minimum {min_price}"
                    if price > max_price:
                        return False, f"Price {price} above maximum {max_price}"
                    if tick_size > 0 and (price % tick_size) != 0:
                        return False, f"Price {price} not valid tick size {tick_size}"
                
                elif filter_type == "MIN_NOTIONAL":
                    min_notional = float(filter_info.get("minNotional", 0))
                    if price and (quantity * price) < min_notional:
                        return False, f"Order value {quantity * price} below minimum {min_notional}"
            
            return True, "Validation passed"
            
        except Exception as e:
            logger.error(f"Error validating order parameters: {e}")
            return False, f"Validation error: {str(e)}"
    
    def close(self) -> None:
        """Close the exchange client and cleanup resources"""
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())
