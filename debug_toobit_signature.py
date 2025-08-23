#!/usr/bin/env python3
"""
Debug script to test Toobit API signature generation
Based on their official documentation examples
"""

import hashlib
import hmac
import time
import requests
import json
import os

def generate_toobit_signature(secret_key: str, params_string: str) -> str:
    """Generate HMAC SHA256 signature exactly as Toobit documentation shows"""
    signature = hmac.new(
        secret_key.encode('utf-8'),
        params_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature

def test_signature():
    """Test signature generation with Toobit documentation example"""
    # Use environment variable for API secret or fallback to documentation example for testing
    secret_key = os.environ.get('TOOBIT_TEST_SECRET', '30lfjDT51iOG1kYZnDoLNynOyMdIcmQyO1XYfxzYOmQfx9tjiI98Pzio4uhZ0Uk2')
    
    # Parameters from their example (SELL order)
    params_string = "symbol=BTCUSDT&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=400&recvWindow=100000&timestamp=1668481902307"
    
    # Expected signature from their documentation
    expected = "8420e499e71cce4a00946db16543198b6bcae01791bdb75a06b5a7098b156468"
    
    generated = generate_toobit_signature(secret_key, params_string)
    
    print(f"Params string: {params_string}")
    print(f"Expected:      {expected}")
    print(f"Generated:     {generated}")
    print(f"Match:         {generated == expected}")
    
    return generated == expected

def test_market_order_signature(api_key: str, secret_key: str):
    """Test signature generation for a market order"""
    # Our current parameters
    timestamp = str(int(time.time() * 1000))
    
    # Market order parameters (no timeInForce for market orders)
    params = {
        'symbol': 'BTCUSDT',
        'side': 'BUY', 
        'type': 'MARKET',
        'quantity': '0.001',
        'timestamp': timestamp
    }
    
    # Sort parameters and create query string  
    sorted_params = sorted(params.items())
    params_string = "&".join([f"{k}={v}" for k, v in sorted_params])
    
    signature = generate_toobit_signature(secret_key, params_string)
    
    print(f"\nMarket Order Test:")
    print(f"Timestamp: {timestamp}")
    print(f"Params string: {params_string}")
    print(f"Signature: {signature}")
    
    # Test actual API call
    url = "https://api.toobit.com/api/v1/futures/order"
    headers = {
        'X-BB-APIKEY': api_key,
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    # Add signature to params
    params['signature'] = signature
    
    print(f"Making test API call...")
    response = requests.post(url, headers=headers, data=params)
    
    print(f"Response status: {response.status_code}")
    print(f"Response body: {response.text}")
    
    return response.status_code, response.text

if __name__ == "__main__":
    print("Testing Toobit signature generation...")
    
    # Check if using environment variable or fallback
    if os.environ.get('TOOBIT_TEST_SECRET'):
        print("Using TOOBIT_TEST_SECRET environment variable")
    else:
        print("Using Toobit documentation example key (fallback)")
    
    # Test with documentation example
    doc_test_passed = test_signature()
    
    if doc_test_passed:
        print("\n✅ Documentation example signature matches!")
        
        print("\nSkipping interactive test - signature generation confirmed working")
    else:
        print("\n❌ Documentation example signature does NOT match!")
        print("There's an issue with the signature generation logic.")