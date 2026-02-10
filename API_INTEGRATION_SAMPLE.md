# OpenCorporates API Integration Guide

> This document explains how the Business Intelligence Pipeline integrates with the OpenCorporates API

## Table of Contents
1. [API Overview](#api-overview)
2. [Authentication](#authentication)
3. [Rate Limits](#rate-limits)
4. [Implementation](#implementation)
5. [Error Handling](#error-handling)
6. [Best Practices](#best-practices)

---

## API Overview

### What is OpenCorporates?
OpenCorporates is the largest open database of companies in the world. Their API provides programmatic access to:
- Company registration details
- Financial data (for UK companies)
- Corporate structure information
- Historical filings

### Why We Use It
This pipeline uses OpenCorporates to:
1. Search for UK companies by industry and incorporation date
2. Retrieve detailed company information including financial statements
3. Filter for companies with balance sheet data (current_assets and fixed_assets)

**Documentation**: https://api.opencorporates.com/documentation/API-Reference

---

## Authentication

### Getting an API Token

1. **Sign Up for Free Account**
   - Visit: https://opencorporates.com/api_accounts/new
   - Provide email and create password
   - Verify email address

2. **Get Your API Token**
   - Log in to OpenCorporates
   - Navigate to: https://opencorporates.com/api_accounts
   - Copy your API token (looks like: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`)

3. **Store Token Securely**
   ```bash
   # Create .env file (never commit this!)
   echo "OPENCORPORATES_API_TOKEN=your-token-here" > .env
   ```

### Using the Token

```python
# In your code
import os
from dotenv import load_dotenv

load_dotenv()
api_token = os.getenv('OPENCORPORATES_API_TOKEN')

# Pass to API calls
params = {"api_token": api_token}
response = requests.get(url, params=params)
```

**⚠️ Security Warning**: NEVER commit your API token to Git!

---

## Rate Limits

### Free Tier Limits
- **500 requests per month**
- **10 requests per minute**
- Resets on the 1st of each month

### Paid Tier Limits
- **10,000+ requests per month**
- **60 requests per minute**
- See pricing: https://opencorporates.com/api_accounts/new

### Monitoring Your Usage

```python
def check_rate_limit(self):
    """Check remaining API quota."""
    headers = response.headers
    
    remaining = headers.get('X-RateLimit-Remaining', 'Unknown')
    reset = headers.get('X-RateLimit-Reset', 'Unknown')
    
    print(f"Remaining requests: {remaining}")
    print(f"Reset time: {reset}")
```

### What Happens When You Hit the Limit?

- API returns **HTTP 429** (Too Many Requests)
- Our code implements exponential backoff:
  ```python
  if response.status_code == 429:
      wait_time = backoff_time * (2 ** retry_count)
      time.sleep(wait_time)
  ```

---

## Implementation

### Search Endpoint

**Purpose**: Find companies matching criteria

**Endpoint**: `GET /v0.4/companies/search`

**Parameters**:
```python
params = {
    "api_token": api_token,
    "jurisdiction_code": "gb",           # United Kingdom
    "q": "accounting",                   # Search query
    "incorporation_date": "1985-01-01:2005-12-31",  # Date range
    "per_page": 100,                     # Results per page
    "page": 1                            # Page number
}
```

**Example Request**:
```python
base_url = "https://api.opencorporates.com/v0.4/companies/search"
response = requests.get(base_url, params=params)
data = response.json()

companies = data['results']['companies']
for company in companies:
    print(company['company']['name'])
```

**Response Structure**:
```json
{
  "results": {
    "companies": [
      {
        "company": {
          "name": "EXAMPLE ACCOUNTING LTD",
          "company_number": "12345678",
          "jurisdiction_code": "gb",
          "incorporation_date": "1995-03-15",
          "registered_address": "...",
          ...
        }
      }
    ]
  }
}
```

### Company Details Endpoint

**Purpose**: Get detailed information for a specific company

**Endpoint**: `GET /v0.4/companies/{jurisdiction_code}/{company_number}`

**Example Request**:
```python
jurisdiction = "gb"
company_number = "12345678"

url = f"https://api.opencorporates.com/v0.4/companies/{jurisdiction}/{company_number}"
params = {"api_token": api_token}

response = requests.get(url, params=params)
company_data = response.json()

# Access financial data
financials = company_data['results']['company'].get('financial_summary', {})
current_assets = financials.get('current_assets', [])
fixed_assets = financials.get('fixed_assets', [])
```

**Response Structure**:
```json
{
  "results": {
    "company": {
      "name": "EXAMPLE LTD",
      "company_number": "12345678",
      "financial_summary": {
        "current_assets": [
          {
            "date": "2023-12-31",
            "value": 150000
          }
        ],
        "fixed_assets": [
          {
            "date": "2023-12-31",
            "value": 500000
          }
        ]
      }
    }
  }
}
```

---

## Error Handling

### Common HTTP Status Codes

| Code | Meaning | Our Response |
|------|---------|--------------|
| 200 | Success | Process data |
| 404 | Company not found | Log and skip |
| 429 | Rate limit exceeded | Exponential backoff retry |
| 500 | Server error | Retry up to 2 times |

### Implementation

```python
def fetch_company_details(self, jurisdiction, company_number, max_retries=2):
    """
    Fetch detailed company information with error handling.
    
    Args:
        jurisdiction (str): Company jurisdiction code (e.g., 'gb')
        company_number (str): Company registration number
        max_retries (int): Number of retry attempts for failed requests
        
    Returns:
        dict: Company details including financial data, or None if fetch fails
    """
    url = f"{self.company_detail_base_url}/{jurisdiction}/{company_number}"
    params = {"api_token": self.api_token}
    
    retry_count = 0
    backoff_time = 2  # Start with 2 second backoff
    
    while retry_count <= max_retries:
        try:
            response = requests.get(url, params=params, timeout=30)
            self.request_count += 1
            
            if response.status_code == 200:
                data = response.json()
                return data.get('results', {}).get('company', {})
            
            elif response.status_code == 429:
                # Rate limit hit - exponential backoff
                wait_time = backoff_time * (2 ** retry_count)
                print(f"⚠ Rate limit - Waiting {wait_time}s before retry")
                time.sleep(wait_time)
                retry_count += 1
                continue
            
            elif response.status_code == 404:
                # Company not found - log and skip
                self.errors.append({
                    "timestamp": datetime.now().isoformat(),
                    "error_type": "404_Not_Found",
                    "company_number": company_number,
                    "message": f"Company not found: {jurisdiction}/{company_number}"
                })
                return None
            
            else:
                # Other error - retry
                if retry_count < max_retries:
                    print(f"⚠ HTTP {response.status_code} - Retrying...")
                    time.sleep(backoff_time)
                    retry_count += 1
                    continue
                else:
                    self.errors.append({
                        "timestamp": datetime.now().isoformat(),
                        "error_type": f"HTTP_{response.status_code}",
                        "company_number": company_number
                    })
                    return None
                    
        except requests.exceptions.Timeout:
            if retry_count < max_retries:
                print(f"⚠ Timeout - Retrying...")
                time.sleep(backoff_time)
                retry_count += 1
                continue
            else:
                self.errors.append({
                    "timestamp": datetime.now().isoformat(),
                    "error_type": "Timeout",
                    "company_number": company_number
                })
                return None
                
        except Exception as e:
            print(f"✗ Unexpected error: {str(e)}")
            return None
    
    return None
```

### Error Logging

All errors are tracked and saved:

```python
def save_error_log(self, filename=None):
    """Save error log to file."""
    if not self.errors:
        print("No errors to log.")
        return None
    
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"error_log_{timestamp}.json"
    
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(self.errors, f, indent=2)
    
    print(f"✓ Error log saved: {filename}")
    print(f"✓ Total errors logged: {len(self.errors)}")
```

---

## Best Practices

### 1. Respect Rate Limits

```python
# Track your request count
self.request_count = 0
self.max_requests = 500  # Free tier limit

if self.request_count >= self.max_requests:
    print("⚠ Approaching rate limit - stopping")
    return
```

### 2. Use Exponential Backoff

```python
# Don't hammer the API when rate limited
backoff_time = 2
for retry in range(max_retries):
    wait_time = backoff_time * (2 ** retry)  # 2s, 4s, 8s...
    time.sleep(wait_time)
```

### 3. Cache Results

```python
# Save intermediate results to avoid re-fetching
def save_checkpoint(self):
    """Save progress to resume later."""
    with open(self.checkpoint_file, 'w') as f:
        json.dump({
            "results": self.results,
            "request_count": self.request_count
        }, f)
```

### 4. Validate Data

```python
# Not all companies have financial data
financial_summary = company.get('financial_summary', {})

if not financial_summary:
    print(f"⚠ No financial data for {company['name']}")
    return None

current_assets = financial_summary.get('current_assets', [])
if not current_assets:
    print(f"⚠ No current assets data")
```

### 5. Use Timeouts

```python
# Prevent hanging on slow connections
response = requests.get(url, params=params, timeout=30)
```

### 6. Log Everything

```python
# Track what's happening
print(f"✓ Fetching company {company_number}")
print(f"✓ Request {self.request_count}/{self.max_requests}")
print(f"⚠ Rate limit hit - backing off")
print(f"✗ Error: {str(e)}")
```

---

## Performance Optimization

### Parallel Requests (Advanced)

For paid tier with higher rate limits:

```python
from concurrent.futures import ThreadPoolExecutor

def fetch_companies_parallel(self, company_list):
    """Fetch multiple companies in parallel."""
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = executor.map(self.fetch_company_details, company_list)
    
    return list(results)
```

**⚠️ Caution**: Only use if you have high rate limits!

### Request Batching

```python
# Fetch in batches to save API calls
def fetch_batch(self, page=1, per_page=100):
    """Fetch up to 100 companies per request."""
    params = {
        "api_token": self.api_token,
        "page": page,
        "per_page": per_page  # Max 100
    }
```

---

## Testing

### Test Mode

Our implementation includes a test mode:

```python
if self.mode == "test":
    self.target_per_industry = 25
    self.max_total_companies = 50
else:
    self.target_per_industry = None  # No limit
    self.max_total_companies = None
```

**Benefits**:
- Test API integration without using full quota
- Faster iteration during development
- Sample data for documentation

### Mock API Responses

For unit testing without API calls:

```python
# tests/test_business_app.py
def test_parse_company_response(mocker):
    """Test parsing without real API call."""
    mock_response = {
        "results": {
            "company": {
                "name": "TEST LTD",
                "company_number": "12345678"
            }
        }
    }
    
    mocker.patch('requests.get', return_value=Mock(
        status_code=200,
        json=lambda: mock_response
    ))
    
    result = fetch_company_details("gb", "12345678")
    assert result['name'] == "TEST LTD"
```

---

## Troubleshooting

### Issue: "Invalid API token"

**Cause**: Token is incorrect or expired  
**Solution**: 
1. Re-copy token from OpenCorporates account
2. Check for extra spaces
3. Verify .env file is being loaded

### Issue: "429 Too Many Requests"

**Cause**: Exceeded rate limit  
**Solution**:
1. Wait until next month for reset
2. Upgrade to paid tier
3. Implement better caching

### Issue: "Companies have no financial data"

**Cause**: Not all UK companies file financials  
**Solution**:
1. This is expected - our code filters for companies WITH data
2. Success rate is typically 30-40%
3. Expand search to more companies

### Issue: "Timeout errors"

**Cause**: Slow network or API  
**Solution**:
1. Increase timeout from 30s to 60s
2. Implement retries (already done)
3. Check your internet connection

---

## API Limits Summary

| Tier | Requests/Month | Requests/Min | Cost |
|------|----------------|--------------|------|
| Free | 500 | 10 | £0 |
| Starter | 10,000 | 60 | £50/month |
| Professional | 100,000 | 120 | £200/month |

**Recommendation**: Start with Free tier for development, upgrade if needed for production

---

## Additional Resources

- **API Documentation**: https://api.opencorporates.com/documentation/API-Reference
- **API Status**: https://api.opencorporates.com/documentation/API-Status
- **Support**: support@opencorporates.com
- **Rate Limits**: https://api.opencorporates.com/documentation/API-Reference#rate-limiting

---

## Change Log

| Date | Version | Changes |
|------|---------|---------|
| 2025-02-10 | 1.0 | Initial documentation |

---

**Questions or Issues?**

If you encounter problems with the API integration:
1. Check this guide first
2. Review error logs in `error_log_*.json`
3. Open an issue in this repository
4. Contact OpenCorporates support for API-specific issues

---

*Last updated: February 2025*
