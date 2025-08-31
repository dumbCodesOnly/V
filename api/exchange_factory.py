"""
Exchange Client Factory - Dynamically create exchange clients based on user preferences
Supports multiple exchanges while maintaining a consistent interface
"""

import logging
from typing import Optional

from .toobit_client import ToobitClient
from .lbank_client import LBankClient
from config import TradingConfig


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