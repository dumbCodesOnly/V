#!/usr/bin/env python3
"""
Test script to verify Toobit API connection and endpoints
Based on official Toobit USDT-M Futures API documentation
"""

import requests
import hashlib
import hmac
import time
import json
import os

# Test API credentials - SECURITY: Load from environment variables
API_KEY = os.environ.get("TOOBIT_API_KEY", "")
API_SECRET = os.environ.get("TOOBIT_API_SECRET", "")

def test_endpoint(endpoint):
    """Test a specific Toobit API endpoint"""
    timestamp = str(int(time.time() * 1000))
    
    # Create parameter string for signature
    params = {
        'timestamp': timestamp,
        'recvWindow': '5000'
    }
    
    params_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    
    # Generate signature
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        params_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    params['signature'] = signature
    
    # Headers
    headers = {
        'X-BB-APIKEY': API_KEY,
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    # Test different endpoints
    base_url = "https://api.toobit.com"
    test_endpoints = [
        "/api/v1/futures/balance",
        "/api/v1/futures/account",
        "/api/v1/exchangeInfo",
        "/api/v1/time"
    ]
    
    for test_endpoint in test_endpoints:
        url = base_url + test_endpoint
        print(f"\nTesting: {url}")
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            print(f"Status Code: {response.status_code}")
            print(f"Response: {response.text[:200]}...")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    print("Testing Toobit API endpoints...")
    test_endpoint("")