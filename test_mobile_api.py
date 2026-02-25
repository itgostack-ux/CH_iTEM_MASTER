#!/usr/bin/env python3
"""
Quick API Tester Script for CH Item Master Mobile APIs
Usage: python3 test_mobile_api.py
"""

import requests
import json
from datetime import datetime

# Configuration
BASE_URL = "http://erpnext.local:8000"
# If you have API token, use it:
API_KEY = ""  # Get from: User Menu → My Settings → API Access → Generate Keys
API_SECRET = ""

# Or use session cookie (after logging in via browser)
SESSION_COOKIE = ""  # Get from browser cookies: 'sid' value

def make_request(endpoint, params=None):
    """Make API request to CH Item Master mobile endpoint"""
    url = f"{BASE_URL}/api/method/ch_item_master.ch_item_master.api_mobile.{endpoint}"
    
    headers = {
        "Content-Type": "application/json",
    }
    
    # Authentication
    if API_KEY and API_SECRET:
        headers["Authorization"] = f"token {API_KEY}:{API_SECRET}"
    elif SESSION_COOKIE:
        cookies = {"sid": SESSION_COOKIE}
    else:
        cookies = None
    
    print(f"\n{'='*80}")
    print(f"📡 Testing: {endpoint}")
    print(f"{'='*80}")
    
    start_time = datetime.now()
    
    try:
        response = requests.get(url, headers=headers, cookies=cookies, params=params or {})
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds() * 1000
        
        print(f"✅ Status: {response.status_code}")
        print(f"⏱️  Time: {duration:.2f}ms")
        print(f"📦 Size: {len(response.content) / 1024:.2f} KB")
        
        if response.status_code == 200:
            data = response.json()
            message = data.get("message", [])
            
            if isinstance(message, list):
                print(f"📊 Records: {len(message)}")
                if message:
                    print(f"\n📋 Sample Data (first 2 records):")
                    print(json.dumps(message[:2], indent=2))
            else:
                print(f"\n📋 Response:")
                print(json.dumps(message, indent=2))
            
            return data
        else:
            print(f"❌ Error: {response.text}")
            return None
            
    except Exception as e:
        print(f"❌ Exception: {str(e)}")
        return None


def main():
    """Test all mobile API endpoints"""
    
    print("="*80)
    print("🚀 CH ITEM MASTER - MOBILE API TESTER")
    print("="*80)
    
    # Test 1: Get Categories
    categories = make_request("get_categories_mobile")
    
    # Test 2: Get Sub-Categories
    if categories and categories.get("message"):
        first_category_id = categories["message"][0].get("id")
        make_request("get_sub_categories_mobile", {"category_id": first_category_id})
    
    # Test 3: Get Models (paginated)
    make_request("get_models_mobile", {"limit": 5, "offset": 0})
    
    # Test 4: Get Channels
    channels = make_request("get_channels_mobile")
    
    # Test 5: Get Model Details (if models exist)
    models = make_request("get_models_mobile", {"limit": 1})
    if models and models.get("message"):
        first_model_id = models["message"][0].get("id")
        if first_model_id:
            make_request("get_model_details_mobile", {"model_id": first_model_id})
    
    # Test 6: Search Items
    make_request("search_items_mobile", {"query": "mobile", "limit": 5})
    
    print("\n" + "="*80)
    print("✅ ALL TESTS COMPLETED!")
    print("="*80)
    print("\n💡 Tips:")
    print("  1. To authenticate, add API_KEY and API_SECRET at the top of this file")
    print("  2. Get API keys: User Menu → My Settings → API Access → Generate Keys")
    print("  3. View API tester page: http://erpnext.local:8000/app/ch-mobile-api-tester")
    print("="*80)


if __name__ == "__main__":
    main()
