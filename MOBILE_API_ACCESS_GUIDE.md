# 📱 CH Item Master - Mobile API Access Guide

**Date:** February 25, 2026  
**API Type:** Frappe REST API (Not FastAPI)  
**Base URL:** `https://yourdomain.com/api/method/ch_item_master.ch_item_master.api_mobile`

---

## 🔍 **Understanding Your API Architecture**

### **NOT FastAPI - These are Frappe REST APIs**

| Aspect | FastAPI | Your Frappe APIs |
|--------|---------|------------------|
| **Framework** | Python FastAPI | Frappe Framework (built-in with ERPNext) |
| **URL Pattern** | `/items/{id}` | `/api/method/app.module.function` |
| **Auto Docs** | Swagger UI at `/docs` | Built-in API Console + Custom Tester Page |
| **Authentication** | OAuth2/JWT/API Keys | API Token, Session Cookie, or API Key/Secret |
| **Response Format** | Direct JSON | Wrapped in `{"message": data}` |
| **Speed** | Very Fast (async) | Fast (synchronous) |
| **Built-in Features** | Type validation, Pydantic | Frappe permissions, DocType integration |

---

## 🌐 **5 Ways to Access Your Mobile APIs**

### **1. Via Built-in ERP API Tester Page** ✅ **BEST FOR DEVELOPMENT**

I've created a beautiful interactive API tester page in your ERPNext!

**Access URL:**
```
http://erpnext.local:8000/app/ch-mobile-api-tester
OR
https://yourdomain.com/app/ch-mobile-api-tester
```

**Features:**
- ✅ Visual interface to test all 8 endpoints
- ✅ Live response preview with JSON formatting
- ✅ Response time and size metrics
- ✅ Copy cURL commands for each API
- ✅ Input fields for all parameters
- ✅ Automatic authentication (uses your logged-in session)
- ✅ Color-coded status indicators

**To Access:**
1. Login to ERPNext: `http://erpnext.local:8000`
2. Navigate to: **Awesomebar** (search bar at top)
3. Type: **"Mobile API Tester"**
4. Press Enter → Opens interactive API testing page!

---

### **2. Via Frappe's Built-in API Console** ✅ **RECOMMENDED FOR TESTING**

Frappe automatically creates API documentation for all your whitelisted endpoints!

**Access:**
```
http://erpnext.local:8000/api/method/ch_item_master.ch_item_master.api_mobile.{function_name}
```

**Example:**
```bash
# Get all categories
http://erpnext.local:8000/api/method/ch_item_master.ch_item_master.api_mobile.get_categories_mobile

# Get models by sub-category
http://erpnext.local:8000/api/method/ch_item_master.ch_item_master.api_mobile.get_models_mobile?sub_category_id=1&limit=10

# Get model details
http://erpnext.local:8000/api/method/ch_item_master.ch_item_master.api_mobile.get_model_details_mobile?model_id=123
```

**Authentication:**
- If logged into ERPNext in browser → Works automatically (session cookie)
- From mobile/external → Need API Token (see Authentication section below)

---

### **3. Via Browser DevTools Console** ✅ **QUICK TESTING**

Open ERPNext in browser, press F12, go to Console tab:

```javascript
// Test get categories
fetch('/api/method/ch_item_master.ch_item_master.api_mobile.get_categories_mobile')
  .then(r => r.json())
  .then(data => console.table(data.message));

// Test get models with pagination
fetch('/api/method/ch_item_master.ch_item_master.api_mobile.get_models_mobile?limit=5&offset=0')
  .then(r => r.json())
  .then(data => console.log(data.message));

// Test search
fetch('/api/method/ch_item_master.ch_item_master.api_mobile.search_items_mobile?query=mobile&limit=10')
  .then(r => r.json())
  .then(data => console.log(data.message));
```

---

### **4. Via cURL (Command Line)** ✅ **FOR SCRIPTS/CI/CD**

#### **A. Using Session Cookie (if logged in)**

```bash
# Step 1: Get session ID (login via browser, inspect cookies, copy 'sid' value)
# OR use curl to login first

# Step 2: Use the session cookie
curl -X GET \
  'http://erpnext.local:8000/api/method/ch_item_master.ch_item_master.api_mobile.get_categories_mobile' \
  -H 'Content-Type: application/json' \
  --cookie 'sid=YOUR_SESSION_ID_HERE'
```

#### **B. Using API Token (Recommended for Production)**

```bash
# Step 1: Generate API token
# Go to: User Menu → My Settings → API Access → Generate Keys
# You'll get: api_key and api_secret

# Step 2: Use the token
curl -X GET \
  'http://erpnext.local:8000/api/method/ch_item_master.ch_item_master.api_mobile.get_categories_mobile' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: token APIKEY:APISECRET'

# Example with parameters
curl -X GET \
  'http://erpnext.local:8000/api/method/ch_item_master.ch_item_master.api_mobile.get_models_mobile?sub_category_id=1&limit=10&offset=0' \
  -H 'Authorization: token YOUR_API_KEY:YOUR_API_SECRET'
```

#### **C. Real Examples (Replace with your credentials)**

```bash
# Get all categories
curl -X GET 'http://erpnext.local:8000/api/method/ch_item_master.ch_item_master.api_mobile.get_categories_mobile' \
  -H 'Authorization: token abc123:xyz789' | jq

# Get models with filtering
curl -X GET 'http://erpnext.local:8000/api/method/ch_item_master.ch_item_master.api_mobile.get_models_mobile?sub_category_id=1&limit=5' \
  -H 'Authorization: token abc123:xyz789' | jq

# Get price by channel ID
curl -X GET 'http://erpnext.local:8000/api/method/ch_item_master.ch_item_master.api_mobile.get_active_price_mobile?item_code=MOB-SAM-001&channel_id=1' \
  -H 'Authorization: token abc123:xyz789' | jq
```

---

### **5. Via Mobile App (Flutter/React Native/Swift/Kotlin)** ✅ **PRODUCTION USE**

#### **A. Flutter Example**

```dart
import 'package:http/http.dart' as http;
import 'dart:convert';

class CHItemMasterAPI {
  final String baseUrl = 'https://yourdomain.com';
  final String apiKey;
  final String apiSecret;
  
  CHItemMasterAPI(this.apiKey, this.apiSecret);
  
  Future<Map<String, dynamic>> _makeRequest(String endpoint, {Map<String, String>? params}) async {
    final uri = Uri.parse('$baseUrl/api/method/ch_item_master.ch_item_master.api_mobile.$endpoint')
        .replace(queryParameters: params);
    
    final response = await http.get(
      uri,
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'token $apiKey:$apiSecret',
      },
    );
    
    if (response.statusCode == 200) {
      return json.decode(response.body);
    } else {
      throw Exception('Failed to load data: ${response.statusCode}');
    }
  }
  
  // Get all categories
  Future<List<dynamic>> getCategories() async {
    final result = await _makeRequest('get_categories_mobile');
    return result['message'];
  }
  
  // Get models by sub-category with pagination
  Future<List<dynamic>> getModels({int? subCategoryId, int limit = 50, int offset = 0}) async {
    final params = {
      'limit': limit.toString(),
      'offset': offset.toString(),
    };
    if (subCategoryId != null) {
      params['sub_category_id'] = subCategoryId.toString();
    }
    
    final result = await _makeRequest('get_models_mobile', params: params);
    return result['message'];
  }
  
  // Get model details by integer ID
  Future<Map<String, dynamic>> getModelDetails(int modelId) async {
    final result = await _makeRequest('get_model_details_mobile', 
        params: {'model_id': modelId.toString()});
    return result['message'];
  }
  
  // Search items
  Future<List<dynamic>> searchItems(String query, {int? channelId, int limit = 20}) async {
    final params = {
      'query': query,
      'limit': limit.toString(),
    };
    if (channelId != null) {
      params['channel_id'] = channelId.toString();
    }
    
    final result = await _makeRequest('search_items_mobile', params: params);
    return result['message'];
  }
}

// Usage
void main() async {
  final api = CHItemMasterAPI('your_api_key', 'your_api_secret');
  
  // Get categories
  final categories = await api.getCategories();
  print('Categories: $categories');
  
  // Get models
  final models = await api.getModels(subCategoryId: 1, limit: 10);
  print('Models: $models');
  
  // Get model details
  final details = await api.getModelDetails(123);
  print('Model Details: $details');
}
```

#### **B. React Native Example**

```javascript
// api/chItemMaster.js
import axios from 'axios';

const BASE_URL = 'https://yourdomain.com';
const API_KEY = 'your_api_key';
const API_SECRET = 'your_api_secret';

const apiClient = axios.create({
  baseURL: BASE_URL,
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `token ${API_KEY}:${API_SECRET}`,
  },
});

export const CHItemMasterAPI = {
  // Get all categories
  getCategories: async () => {
    const response = await apiClient.get(
      '/api/method/ch_item_master.ch_item_master.api_mobile.get_categories_mobile'
    );
    return response.data.message;
  },

  // Get sub-categories by category ID
  getSubCategories: async (categoryId = null) => {
    const params = categoryId ? { category_id: categoryId } : {};
    const response = await apiClient.get(
      '/api/method/ch_item_master.ch_item_master.api_mobile.get_sub_categories_mobile',
      { params }
    );
    return response.data.message;
  },

  // Get models with pagination
  getModels: async ({ subCategoryId = null, limit = 50, offset = 0 }) => {
    const params = { limit, offset };
    if (subCategoryId) params.sub_category_id = subCategoryId;
    
    const response = await apiClient.get(
      '/api/method/ch_item_master.ch_item_master.api_mobile.get_models_mobile',
      { params }
    );
    return response.data.message;
  },

  // Get model details
  getModelDetails: async (modelId) => {
    const response = await apiClient.get(
      '/api/method/ch_item_master.ch_item_master.api_mobile.get_model_details_mobile',
      { params: { model_id: modelId } }
    );
    return response.data.message;
  },

  // Get price by channel ID
  getPrice: async (itemCode, channelId) => {
    const response = await apiClient.get(
      '/api/method/ch_item_master.ch_item_master.api_mobile.get_active_price_mobile',
      { params: { item_code: itemCode, channel_id: channelId } }
    );
    return response.data.message;
  },

  // Search items
  searchItems: async (query, channelId = null, limit = 20) => {
    const params = { query, limit };
    if (channelId) params.channel_id = channelId;
    
    const response = await apiClient.get(
      '/api/method/ch_item_master.ch_item_master.api_mobile.search_items_mobile',
      { params }
    );
    return response.data.message;
  },
};

// Usage in React Native component
import React, { useEffect, useState } from 'react';
import { View, Text, FlatList } from 'react-native';
import { CHItemMasterAPI } from './api/chItemMaster';

const CategoriesScreen = () => {
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const loadCategories = async () => {
      try {
        const data = await CHItemMasterAPI.getCategories();
        setCategories(data);
      } catch (error) {
        console.error('Failed to load categories:', error);
      } finally {
        setLoading(false);
      }
    };

    loadCategories();
  }, []);

  return (
    <FlatList
      data={categories}
      keyExtractor={(item) => item.id.toString()}
      renderItem={({ item }) => (
        <View>
          <Text>ID: {item.id}</Text>
          <Text>Name: {item.name}</Text>
        </View>
      )}
    />
  );
};
```

#### **C. Swift (iOS) Example**

```swift
import Foundation

class CHItemMasterAPI {
    private let baseURL = "https://yourdomain.com"
    private let apiKey = "your_api_key"
    private let apiSecret = "your_api_secret"
    
    private func makeRequest<T: Decodable>(
        endpoint: String,
        params: [String: String] = [:]
    ) async throws -> T {
        var components = URLComponents(string: "\(baseURL)/api/method/ch_item_master.ch_item_master.api_mobile.\(endpoint)")!
        components.queryItems = params.map { URLQueryItem(name: $0.key, value: $0.value) }
        
        var request = URLRequest(url: components.url!)
        request.httpMethod = "GET"
        request.addValue("application/json", forHTTPHeaderField: "Content-Type")
        request.addValue("token \(apiKey):\(apiSecret)", forHTTPHeaderField: "Authorization")
        
        let (data, response) = try await URLSession.shared.data(for: request)
        
        guard let httpResponse = response as? HTTPURLResponse,
              httpResponse.statusCode == 200 else {
            throw APIError.requestFailed
        }
        
        let result = try JSONDecoder().decode(APIResponse<T>.self, from: data)
        return result.message
    }
    
    func getCategories() async throws -> [Category] {
        return try await makeRequest(endpoint: "get_categories_mobile")
    }
    
    func getModels(subCategoryId: Int? = nil, limit: Int = 50, offset: Int = 0) async throws -> [Model] {
        var params = [
            "limit": String(limit),
            "offset": String(offset)
        ]
        if let subCategoryId = subCategoryId {
            params["sub_category_id"] = String(subCategoryId)
        }
        return try await makeRequest(endpoint: "get_models_mobile", params: params)
    }
}

// Models
struct APIResponse<T: Decodable>: Decodable {
    let message: T
}

struct Category: Decodable {
    let id: Int
    let name: String
    let itemGroup: String
    let isActive: Int
    
    enum CodingKeys: String, CodingKey {
        case id, name
        case itemGroup = "item_group"
        case isActive = "is_active"
    }
}

struct Model: Decodable {
    let id: Int
    let name: String
    let subCategory: String
    let manufacturer: String
    let brand: String
    
    enum CodingKeys: String, CodingKey {
        case id, name, manufacturer, brand
        case subCategory = "sub_category"
    }
}

// Usage
Task {
    let api = CHItemMasterAPI()
    
    do {
        let categories = try await api.getCategories()
        print("Categories: \(categories)")
        
        let models = try await api.getModels(subCategoryId: 1, limit: 10)
        print("Models: \(models)")
    } catch {
        print("Error: \(error)")
    }
}
```

---

## 🔐 **Authentication Methods**

### **1. API Token (Recommended for Production)**

#### **Step 1: Generate API Key/Secret**

1. Login to ERPNext
2. Go to: **User Menu** (top right) → **My Settings**
3. Scroll to: **API Access** section
4. Click: **Generate Keys**
5. Copy both:
   - `api_key` (e.g., `abc123def456`)
   - `api_secret` (e.g., `xyz789uvw012`)

#### **Step 2: Use in API Calls**

```bash
# cURL
curl -H 'Authorization: token YOUR_API_KEY:YOUR_API_SECRET' \
  'http://erpnext.local:8000/api/method/ch_item_master.ch_item_master.api_mobile.get_categories_mobile'

# HTTP Header
Authorization: token abc123def456:xyz789uvw012
```

#### **Step 3: Store Securely in Mobile App**

```dart
// Flutter - Secure Storage
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

final storage = FlutterSecureStorage();

// Store
await storage.write(key: 'erp_api_key', value: 'abc123');
await storage.write(key: 'erp_api_secret', value: 'xyz789');

// Retrieve
final apiKey = await storage.read(key: 'erp_api_key');
final apiSecret = await storage.read(key: 'erp_api_secret');
```

---

### **2. Session Cookie (For Web/Browser)**

If accessing from logged-in ERPNext session:

```javascript
// Automatically uses session cookie
fetch('/api/method/ch_item_master.ch_item_master.api_mobile.get_categories_mobile')
  .then(r => r.json())
  .then(data => console.log(data.message));
```

---

### **3. OAuth (Future Enhancement)**

Not currently implemented. Frappe supports OAuth2, but requires setup.

---

## 📊 **API Response Format**

### **Standard Frappe Response Structure**

```json
{
  "message": [/* Your actual data array */],
  "exc": null,  // Exception details if error occurred
  "exc_type": null
}
```

### **Example Response - Get Categories**

```json
{
  "message": [
    {
      "id": 1,
      "name": "Mobiles",
      "item_group": "Products",
      "is_active": 1
    },
    {
      "id": 2,
      "name": "laptop spares",
      "item_group": "Products",
      "is_active": 1
    }
  ]
}
```

### **Example Response - Get Model Details**

```json
{
  "message": {
    "model_id": 123,
    "sub_category": "Mobile-Smartphone",
    "sub_category_id": 5,
    "category": "Mobiles",
    "category_id": 1,
    "manufacturer": "Samsung",
    "brand": "Samsung",
    "has_variants": true,
    "spec_selectors": [
      {
        "spec": "Color",
        "values": ["Black", "White", "Blue"]
      },
      {
        "spec": "Storage",
        "values": ["128GB", "256GB", "512GB"]
      }
    ],
    "property_specs": [
      {
        "spec": "RAM",
        "value": "8GB"
      }
    ],
    "hsn_code": "8517",
    "gst_rate": 18.0
  }
}
```

---

## 🔧 **Error Handling**

### **Common HTTP Status Codes**

| Code | Meaning | Solution |
|------|---------|----------|
| 200 | Success | Everything worked! |
| 401 | Unauthorized | Check API token or session cookie |
| 403 | Forbidden | User doesn't have permission |
| 404 | Not Found | Check endpoint URL spelling |
| 500 | Server Error | Check server logs |

### **Error Response Example**

```json
{
  "exc": "App ch_item_master is not installed",
  "exc_type": "AppNotInstalledError",
  "_server_messages": "[\"Error details here\"]"
}
```

### **Mobile App Error Handling**

```dart
try {
  final categories = await api.getCategories();
  print('Success: $categories');
} on DioError catch (e) {
  if (e.response?.statusCode == 401) {
    print('Authentication failed. Please login again.');
  } else if (e.response?.statusCode == 500) {
    print('Server error. Please try again later.');
  } else {
    print('Error: ${e.message}');
  }
}
```

---

## 📦 **Complete API Endpoint Reference**

| # | Endpoint | Parameters | Response Size | Use Case |
|---|----------|------------|---------------|----------|
| 1 | `get_categories_mobile` | None | ~500 bytes | Dropdown menus |
| 2 | `get_sub_categories_mobile` | `category_id` (opt) | ~2KB | Filter by category |
| 3 | `get_models_mobile` | `sub_category_id`, `limit`, `offset` | ~5KB/100 | Product listing |
| 4 | `get_channels_mobile` | None | ~300 bytes | Price channel dropdown |
| 5 | `get_model_details_mobile` | `model_id` (req) | ~1KB | Product detail page |
| 6 | `get_active_price_mobile` | `item_code`, `channel_id` | ~500 bytes | Price lookup |
| 7 | `get_ready_reckoner_mobile` | `model_id`, `channel_id`, `limit` | ~10KB/100 | Bulk pricing grid |
| 8 | `search_items_mobile` | `query`, `channel_id`, `limit` | ~3KB/20 | Search functionality |

---

## ⚡ **Performance Tips**

### **1. Use Pagination**

```dart
// BAD - Load all 10,000 models at once
final models = await api.getModels(limit: 10000);  // ❌ Slow!

// GOOD - Load 50 at a time
final models = await api.getModels(limit: 50, offset: 0);  // ✅ Fast!

// GOOD - Infinite scroll
int offset = 0;
while (true) {
  final batch = await api.getModels(limit: 50, offset: offset);
  if (batch.isEmpty) break;
  allModels.addAll(batch);
  offset += 50;
}
```

### **2. Cache Master Data**

```dart
// Cache categories locally (they rarely change)
final prefs = await SharedPreferences.getInstance();

// First load - fetch from API
if (!prefs.containsKey('categories_cache')) {
  final categories = await api.getCategories();
  await prefs.setString('categories_cache', jsonEncode(categories));
} else {
  // Use cached data
  final cached = jsonDecode(prefs.getString('categories_cache')!);
}

// Refresh cache daily
final lastSync = prefs.getInt('last_sync');
if (DateTime.now().difference(DateTime.fromMillisecondsSinceEpoch(lastSync)).inDays > 1) {
  // Refresh cache
}
```

### **3. Parallel Requests**

```dart
// BAD - Sequential loading (slow)
final categories = await api.getCategories();
final channels = await api.getChannels();
final models = await api.getModels();

// GOOD - Parallel loading (fast)
final results = await Future.wait([
  api.getCategories(),
  api.getChannels(),
  api.getModels(limit: 10),
]);
final categories = results[0];
final channels = results[1];
final models = results[2];
```

---

## 🎯 **Testing Checklist**

### **Before Go-Live**

- [ ] Test all 8 endpoints with valid data
- [ ] Test with invalid parameters (error handling)
- [ ] Test authentication failure scenarios
- [ ] Test with slow network (3G simulation)
- [ ] Test pagination boundaries (offset=0, last page)
- [ ] Test with special characters in search query
- [ ] Measure actual bandwidth savings vs string IDs
- [ ] Load test with 100 concurrent users
- [ ] Test API rate limiting (if configured)
- [ ] Document all API keys/secrets securely

---

## 🚀 **Next Steps**

1. **Open API Tester Page:** `http://erpnext.local:8000/app/ch-mobile-api-tester`
2. **Generate API Keys:** User Settings → API Access → Generate Keys
3. **Test Each Endpoint:** Use the interactive page
4. **Copy cURL Examples:** For documentation
5. **Integrate in Mobile App:** Use code examples above
6. **Deploy to Production:** Update base URL in mobile app

---

## 📞 **Support**

If you have issues:

1. **Check Frappe Logs:**
   ```bash
   tail -f /home/palla/erpnext-bench/logs/erpnext.local.log
   ```

2. **Check Network Tab:** Browser DevTools → Network → See actual requests

3. **Test with cURL:** Isolate if it's app-specific or API issue

4. **Check Permissions:** Ensure user has access to CH Item Master module

---

**🎉 Your APIs are Ready! Start building your mobile app with 75% smaller payloads!**
