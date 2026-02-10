import requests
import csv
import time
from datetime import datetime
import json
import os

from financial_analyzer import analyze_results

class BoomerBusinessFinder:
    """
    Find UK businesses owned by retiring Baby Boomers (60-70 years old)
    Target: Boring, cash-flowing businesses in accounting, laundromats, storage, vending/ATM
    
    VERSION 2.1: Filters for companies WITH balance sheet data (current_assets OR fixed_assets)
    """
    
    def __init__(self, api_token=None, mode="test"):
        self.base_url = "https://api.opencorporates.com/v0.4/companies/search"
        self.company_detail_base_url = "https://api.opencorporates.com/v0.4/companies"
        self.country_code = "gb"  # UK
        self.mode = mode  # "test" or "production"
        
        # API Token
        self.api_token = api_token
        
        if not self.api_token:
            raise ValueError("API token required")
        
        # Configure based on mode
        if self.mode == "test":
            # TEST MODE: 25 Accounting + 25 Vending/ATM (with balance sheet data)
            self.industries = {
                "Accounting": ["accounting", "bookkeeping", "chartered accountant"],
                "ATM_Vending": ["vending", "ATM operator"]
            }
            self.target_per_industry = 25
            self.max_total_companies = 50
        else:
            # PRODUCTION MODE: All industries, no limits
            self.industries = {
                "Accounting": ["accounting", "bookkeeping", "chartered accountant", "tax services"],
                "Laundromats": ["laundromat", "launderette", "dry cleaning"],
                "Storage": ["storage", "self storage", "warehousing"],
                "ATM_Vending": ["vending", "ATM operator"]
            }
            self.target_per_industry = None  # No limit
            self.max_total_companies = None  # No limit
        
        # Filter for businesses incorporated 20-40 years ago (owners likely 60-70)
        current_year = datetime.now().year
        self.incorporation_date_start = f"{current_year - 40}-01-01"  # 1985
        self.incorporation_date_end = f"{current_year - 20}-12-31"    # 2005
        
        self.results = []
        self.request_count = 0
        self.max_requests = 500  # Free tier limit
        self.industry_counts = {}  # Track count per industry
        
        # NEW: Tracking for balance sheet filtering
        self.companies_checked = 0  # Total companies examined
        self.companies_with_financials = 0  # Companies that have ANY financial data
        self.companies_without_financials = 0  # Companies with no financials at all
        self.companies_with_balance_sheet = 0  # Companies with current_assets OR fixed_assets (OUR TARGET)
        self.companies_without_balance_sheet = 0  # Has financials but no balance sheet data
        
        # NEW: Error tracking
        self.errors = []
        
        # NEW: Checkpoint system
        self.checkpoint_file = f"checkpoint_{mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
    def save_checkpoint(self):
        """
        Save current progress to checkpoint file
        """
        checkpoint_data = {
            "timestamp": datetime.now().isoformat(),
            "mode": self.mode,
            "results_count": len(self.results),
            "request_count": self.request_count,
            "companies_checked": self.companies_checked,
            "companies_with_financials": self.companies_with_financials,
            "companies_without_financials": self.companies_without_financials,
            "companies_with_balance_sheet": self.companies_with_balance_sheet,
            "companies_without_balance_sheet": self.companies_without_balance_sheet,
            "industry_counts": self.industry_counts,
            "results": self.results
        }
        
        try:
            with open(self.checkpoint_file, 'w', encoding='utf-8') as f:
                json.dump(checkpoint_data, f, indent=2)
            print(f"  💾 Checkpoint saved: {len(self.results)} companies")
        except Exception as e:
            print(f"  ⚠ Checkpoint save failed: {str(e)}")
    
    def load_checkpoint(self, checkpoint_file):
        """
        Load progress from checkpoint file
        """
        try:
            with open(checkpoint_file, 'r', encoding='utf-8') as f:
                checkpoint_data = json.load(f)
            
            self.results = checkpoint_data.get("results", [])
            self.request_count = checkpoint_data.get("request_count", 0)
            self.companies_checked = checkpoint_data.get("companies_checked", 0)
            self.companies_with_financials = checkpoint_data.get("companies_with_financials", 0)
            self.companies_without_financials = checkpoint_data.get("companies_without_financials", 0)
            self.companies_with_balance_sheet = checkpoint_data.get("companies_with_balance_sheet", 0)
            self.companies_without_balance_sheet = checkpoint_data.get("companies_without_balance_sheet", 0)
            self.industry_counts = checkpoint_data.get("industry_counts", {})
            
            print(f"✓ Checkpoint loaded: {len(self.results)} companies restored")
            return True
        except Exception as e:
            print(f"✗ Failed to load checkpoint: {str(e)}")
            return False
    
    def fetch_company_details(self, jurisdiction, company_number, max_retries=2):
        """
        Fetch detailed company information including financial data
        
        Args:
            jurisdiction: Company jurisdiction code
            company_number: Company registration number
            max_retries: Number of retry attempts for failed requests
            
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
                    print(f"  ⚠ Rate limit (429) - Waiting {wait_time}s before retry {retry_count + 1}/{max_retries}")
                    time.sleep(wait_time)
                    retry_count += 1
                    continue
                
                elif response.status_code == 404:
                    # Company not found - log and skip
                    error_msg = f"Company not found (404): {jurisdiction}/{company_number}"
                    self.errors.append({
                        "timestamp": datetime.now().isoformat(),
                        "error_type": "404_Not_Found",
                        "jurisdiction": jurisdiction,
                        "company_number": company_number,
                        "message": error_msg
                    })
                    return None
                
                else:
                    # Other error - log and retry
                    error_msg = f"HTTP {response.status_code}: {jurisdiction}/{company_number}"
                    if retry_count < max_retries:
                        print(f"  ⚠ {error_msg} - Retrying...")
                        time.sleep(backoff_time)
                        retry_count += 1
                        continue
                    else:
                        self.errors.append({
                            "timestamp": datetime.now().isoformat(),
                            "error_type": f"HTTP_{response.status_code}",
                            "jurisdiction": jurisdiction,
                            "company_number": company_number,
                            "message": error_msg
                        })
                        return None
                
            except requests.exceptions.Timeout:
                error_msg = f"Timeout: {jurisdiction}/{company_number}"
                if retry_count < max_retries:
                    print(f"  ⚠ {error_msg} - Retrying...")
                    time.sleep(backoff_time)
                    retry_count += 1
                    continue
                else:
                    self.errors.append({
                        "timestamp": datetime.now().isoformat(),
                        "error_type": "Timeout",
                        "jurisdiction": jurisdiction,
                        "company_number": company_number,
                        "message": error_msg
                    })
                    return None
            
            except requests.exceptions.RequestException as e:
                error_msg = f"Request failed: {jurisdiction}/{company_number} - {str(e)}"
                self.errors.append({
                    "timestamp": datetime.now().isoformat(),
                    "error_type": "Request_Exception",
                    "jurisdiction": jurisdiction,
                    "company_number": company_number,
                    "message": error_msg
                })
                return None
        
        return None
    
    def has_financial_data(self, company_details):
        """
        Check if company has any financial data available
        
        Args:
            company_details: Detailed company information from API
            
        Returns:
            tuple: (has_data: bool, financial_data: dict or None)
        """
        if not company_details:
            return False, None
        
        # Check for financial data in various possible locations
        financial_data = {}
        
        # Check for financials in company_details
        if 'financials' in company_details and company_details['financials']:
            financial_data['financials'] = company_details['financials']
        
        # Check for accounts/filings
        if 'accounts' in company_details and company_details['accounts']:
            financial_data['accounts'] = company_details['accounts']
        
        # Check for latest_accounts
        if 'latest_accounts' in company_details and company_details['latest_accounts']:
            financial_data['latest_accounts'] = company_details['latest_accounts']
        
        # Check for financial summary
        if 'financial_summary' in company_details and company_details['financial_summary']:
            financial_data['financial_summary'] = company_details['financial_summary']
        
        # Any financial data found?
        has_data = len(financial_data) > 0
        
        return has_data, financial_data if has_data else None
    
    def has_balance_sheet_data(self, financial_data):
        """
        Check if company has Current Assets OR Fixed Assets data
        LENIENT: Accepts if array exists with structure, even if values are null
        
        Args:
            financial_data: Financial data dictionary
            
        Returns:
            tuple: (has_data: bool, asset_info: dict)
        """
        if not financial_data:
            return False, None
        
        asset_info = {
            "has_current_assets": False,
            "has_fixed_assets": False,
            "current_assets_entries": 0,
            "fixed_assets_entries": 0
        }
        
        # Check in financial_summary (most common location based on your data)
        if 'financial_summary' in financial_data:
            fin_summary = financial_data['financial_summary']
            
            # Check for current_assets
            if 'current_assets' in fin_summary:
                ca = fin_summary['current_assets']
                # Accept if it's a non-null list with at least one entry (even if value is null)
                if ca is not None and isinstance(ca, list) and len(ca) > 0:
                    asset_info["has_current_assets"] = True
                    asset_info["current_assets_entries"] = len(ca)
            
            # Check for fixed_assets
            if 'fixed_assets' in fin_summary:
                fa = fin_summary['fixed_assets']
                # Accept if it's a non-null list with at least one entry (even if value is null)
                if fa is not None and isinstance(fa, list) and len(fa) > 0:
                    asset_info["has_fixed_assets"] = True
                    asset_info["fixed_assets_entries"] = len(fa)
        
        # Also check other locations (accounts, latest_accounts, financials)
        for key in ['accounts', 'latest_accounts', 'financials']:
            if key in financial_data and financial_data[key]:
                data_section = financial_data[key]
                
                # Handle if it's a list
                if isinstance(data_section, list):
                    for item in data_section:
                        if isinstance(item, dict):
                            # Check for current_assets
                            if 'current_assets' in item:
                                ca = item['current_assets']
                                if ca is not None and isinstance(ca, list) and len(ca) > 0:
                                    asset_info["has_current_assets"] = True
                                    asset_info["current_assets_entries"] = max(
                                        asset_info["current_assets_entries"], 
                                        len(ca)
                                    )
                            
                            # Check for fixed_assets
                            if 'fixed_assets' in item:
                                fa = item['fixed_assets']
                                if fa is not None and isinstance(fa, list) and len(fa) > 0:
                                    asset_info["has_fixed_assets"] = True
                                    asset_info["fixed_assets_entries"] = max(
                                        asset_info["fixed_assets_entries"],
                                        len(fa)
                                    )
                
                # Handle if it's a dict
                elif isinstance(data_section, dict):
                    # Check for current_assets
                    if 'current_assets' in data_section:
                        ca = data_section['current_assets']
                        if ca is not None and isinstance(ca, list) and len(ca) > 0:
                            asset_info["has_current_assets"] = True
                            asset_info["current_assets_entries"] = len(ca)
                    
                    # Check for fixed_assets
                    if 'fixed_assets' in data_section:
                        fa = data_section['fixed_assets']
                        if fa is not None and isinstance(fa, list) and len(fa) > 0:
                            asset_info["has_fixed_assets"] = True
                            asset_info["fixed_assets_entries"] = len(fa)
        
        # Company passes if it has EITHER current_assets OR fixed_assets (lenient)
        has_data = asset_info["has_current_assets"] or asset_info["has_fixed_assets"]
        
        return has_data, asset_info if has_data else None
    
    def search_companies(self, keyword, industry_category, per_page=30, max_pages=3):
        """
        Search for companies by keyword with filters
        Only saves companies that have balance sheet data (current_assets OR fixed_assets)
        
        Args:
            keyword: Search term (e.g., "laundromat")
            industry_category: Category name for tracking
            per_page: Results per page (max 100)
            max_pages: Maximum pages to fetch per keyword
        """
        # Check if we've reached the target for this industry (test mode only)
        if self.mode == "test":
            current_count = self.industry_counts.get(industry_category, 0)
            if current_count >= self.target_per_industry:
                print(f"  ✓ Target reached for {industry_category}: {current_count}/{self.target_per_industry}")
                return 0
        
        # Check if we've reached total company limit (test mode only)
        if self.max_total_companies and len(self.results) >= self.max_total_companies:
            print(f"  ✓ Total company limit reached: {len(self.results)}/{self.max_total_companies}")
            return 0
        
        print(f"\n{'='*60}")
        print(f"Searching: {keyword} ({industry_category})")
        print(f"{'='*60}")
        
        page = 1
        companies_found = 0
        
        while page <= max_pages and self.request_count < self.max_requests:
            # Check limits again before each request
            if self.max_total_companies and len(self.results) >= self.max_total_companies:
                print(f"  ✓ Total limit reached, stopping search")
                break
            
            if self.mode == "test":
                current_count = self.industry_counts.get(industry_category, 0)
                if current_count >= self.target_per_industry:
                    print(f"  ✓ Industry target reached, stopping search")
                    break
            
            params = {
                "q": keyword,
                "country_code": self.country_code,
                "current_status": "Active",
                "incorporation_date": f"{self.incorporation_date_start}:{self.incorporation_date_end}",
                "per_page": per_page,
                "page": page,
                "api_token": self.api_token
            }
            
            try:
                print(f"  → Page {page} | Search Request #{self.request_count + 1}/{self.max_requests}")
                
                response = requests.get(self.base_url, params=params, timeout=30)
                self.request_count += 1
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Check if we have results
                    if 'results' not in data or 'companies' not in data['results']:
                        print(f"  ✗ No results found")
                        break
                    
                    companies = data['results']['companies']
                    
                    if not companies:
                        print(f"  ✗ No more companies on page {page}")
                        break
                    
                    print(f"  ✓ Found {len(companies)} companies on this page")
                    print(f"  📊 Checking each for balance sheet data...")
                    
                    # Process each company - fetch details and check for balance sheet data
                    for idx, company_data in enumerate(companies, 1):
                        # Check limits before processing each company
                        if self.max_total_companies and len(self.results) >= self.max_total_companies:
                            print(f"  ✓ Reached total limit during processing")
                            break
                        
                        if self.mode == "test":
                            current_count = self.industry_counts.get(industry_category, 0)
                            if current_count >= self.target_per_industry:
                                print(f"  ✓ Reached industry limit during processing")
                                break
                        
                        company = company_data.get('company', {})
                        jurisdiction = company.get('jurisdiction_code', '')
                        company_number = company.get('company_number', '')
                        company_name = company.get('name', 'Unknown')
                        
                        if not jurisdiction or not company_number:
                            continue
                        
                        self.companies_checked += 1
                        
                        # Fetch detailed company information
                        print(f"    [{idx}/{len(companies)}] Checking: {company_name[:40]}...", end=' ')
                        
                        company_details = self.fetch_company_details(jurisdiction, company_number)
                        
                        # Delay between detail fetches (1.5 seconds)
                        time.sleep(1.5)
                        
                        # Check if company has any financial data
                        has_financials, financial_data = self.has_financial_data(company_details)
                        
                        if has_financials:
                            self.companies_with_financials += 1
                            
                            # NEW: Check specifically for balance sheet data
                            has_balance_sheet, asset_info = self.has_balance_sheet_data(financial_data)
                            
                            if has_balance_sheet:
                                # Build status message
                                status_msg = "✓ HAS BALANCE SHEET"
                                if asset_info["has_current_assets"] and asset_info["has_fixed_assets"]:
                                    status_msg += " (Both)"
                                elif asset_info["has_current_assets"]:
                                    status_msg += f" (Current: {asset_info['current_assets_entries']}yr)"
                                else:
                                    status_msg += f" (Fixed: {asset_info['fixed_assets_entries']}yr)"
                                
                                print(status_msg)
                                self.companies_with_balance_sheet += 1
                                
                                # Extract and save company info
                                company_info = self.extract_company_info(
                                    company, 
                                    keyword, 
                                    industry_category,
                                    company_details,
                                    financial_data
                                )
                                
                                if company_info:
                                    # Add balance sheet metadata
                                    company_info["Has_Current_Assets"] = "Yes" if asset_info["has_current_assets"] else "No"
                                    company_info["Has_Fixed_Assets"] = "Yes" if asset_info["has_fixed_assets"] else "No"
                                    company_info["Current_Assets_Years"] = asset_info["current_assets_entries"]
                                    company_info["Fixed_Assets_Years"] = asset_info["fixed_assets_entries"]
                                    
                                    self.results.append(company_info)
                                    companies_found += 1
                                    
                                    # Update industry count
                                    self.industry_counts[industry_category] = self.industry_counts.get(industry_category, 0) + 1
                                    
                                    # Save checkpoint every 10 companies with balance sheet data
                                    if len(self.results) % 10 == 0:
                                        self.save_checkpoint()
                            else:
                                print(f"✗ Financials but NO balance sheet")
                                self.companies_without_balance_sheet += 1
                        else:
                            print(f"✗ No financials")
                            self.companies_without_financials += 1
                    
                    # Progress summary after each page
                    print(f"\n  📈 Progress:")
                    print(f"     Total checked: {self.companies_checked}")
                    print(f"     → With balance sheet: {self.companies_with_balance_sheet} ✓ (SAVED)")
                    print(f"     → Financials only: {self.companies_without_balance_sheet}")
                    print(f"     → No financials: {self.companies_without_financials}")
                    
                    # Check if we should continue
                    if self.max_total_companies and len(self.results) >= self.max_total_companies:
                        break
                    
                    if self.mode == "test":
                        current_count = self.industry_counts.get(industry_category, 0)
                        if current_count >= self.target_per_industry:
                            break
                    
                    # Check if there are more pages
                    total_pages = data.get('results', {}).get('total_pages', 0)
                    if page >= total_pages:
                        print(f"  ℹ Reached last page ({total_pages})")
                        break
                    
                    page += 1
                    
                    # Rate limiting: 2 second delay between search requests
                    time.sleep(2)
                    
                elif response.status_code == 429:
                    print(f"  ⚠ Rate limit hit! Waiting 60 seconds...")
                    time.sleep(60)
                    continue
                    
                else:
                    print(f"  ✗ Error: Status code {response.status_code}")
                    print(f"  Response: {response.text[:200]}")
                    break
                    
            except requests.exceptions.RequestException as e:
                print(f"  ✗ Request failed: {str(e)}")
                break
        
        print(f"  ✓ Total companies with balance sheet for '{keyword}': {companies_found}")
        return companies_found
    
    def extract_company_info(self, company, keyword, industry_category, company_details=None, financial_data=None):
        """
        Extract relevant information from company data including financial data
        """
        try:
            company_info = {
                "Industry_Category": industry_category,
                "Search_Keyword": keyword,
                "Company_Name": company.get('name', 'N/A'),
                "Company_Number": company.get('company_number', 'N/A'),
                "Jurisdiction": company.get('jurisdiction_code', 'N/A'),
                "Incorporation_Date": company.get('incorporation_date', 'N/A'),
                "Company_Type": company.get('company_type', 'N/A'),
                "Status": company.get('current_status', 'N/A'),
                "Registered_Address": company.get('registered_address_in_full', 'N/A'),
                "OpenCorporates_URL": company.get('opencorporates_url', 'N/A'),
            }
            
            # Get officer/director information if available
            officers_url = company.get('officers_url', None)
            company_info["Officers_Available"] = "Yes" if officers_url else "No"
            company_info["Officers_URL"] = officers_url if officers_url else "N/A"
            
            # Add financial data as JSON string
            if financial_data:
                company_info["Financial_Data"] = json.dumps(financial_data, indent=2)
            else:
                company_info["Financial_Data"] = "N/A"
            
            # Add any additional details from company_details if available
            if company_details:
                # Industry code
                if 'industry_codes' in company_details:
                    company_info["Industry_Codes"] = json.dumps(company_details.get('industry_codes', []))
                else:
                    company_info["Industry_Codes"] = "N/A"
                
                # Previous names
                if 'previous_names' in company_details and company_details['previous_names']:
                    company_info["Previous_Names"] = json.dumps(company_details.get('previous_names', []))
                else:
                    company_info["Previous_Names"] = "N/A"
            
            return company_info
            
        except Exception as e:
            print(f"  ⚠ Error extracting company info: {str(e)}")
            return None
    
    def run_search(self):
        """
        Execute the full search across all industries and keywords
        Only returns companies WITH balance sheet data (current_assets OR fixed_assets)
        """
        mode_label = "TEST MODE" if self.mode == "test" else "PRODUCTION MODE"
        
        print("\n" + "="*60)
        print(f"BOOMER BUSINESS FINDER V2.1 - UK Edition - {mode_label}")
        print("="*60)
        print(f"Target: Companies incorporated {self.incorporation_date_start} to {self.incorporation_date_end}")
        print(f"Location: United Kingdom")
        print(f"Filter: ONLY companies WITH balance sheet data")
        print(f"        (current_assets OR fixed_assets arrays)")
        
        if self.mode == "test":
            print(f"Mode: TEST - Target {self.max_total_companies} companies WITH balance sheet")
            print(f"  → 25 Accounting/Bookkeeping")
            print(f"  → 25 Vending/ATM Operators")
        else:
            print(f"Mode: PRODUCTION - No limits")
            print(f"  → All 4 industries")
        
        print(f"API Request Limit: {self.max_requests}")
        print("="*60)
        
        total_found = 0
        
        for industry_category, keywords in self.industries.items():
            print(f"\n{'#'*60}")
            print(f"INDUSTRY: {industry_category}")
            if self.mode == "test":
                current_count = self.industry_counts.get(industry_category, 0)
                print(f"Progress: {current_count}/{self.target_per_industry} with balance sheet")
            print(f"{'#'*60}")
            
            for keyword in keywords:
                # Check if we've reached limits
                if self.request_count >= self.max_requests:
                    print(f"\n⚠ Reached API request limit ({self.max_requests}). Stopping search.")
                    break
                
                if self.max_total_companies and len(self.results) >= self.max_total_companies:
                    print(f"\n✓ Reached total company limit ({self.max_total_companies}). Stopping search.")
                    break
                
                if self.mode == "test":
                    current_count = self.industry_counts.get(industry_category, 0)
                    if current_count >= self.target_per_industry:
                        print(f"\n✓ Reached target for {industry_category}. Moving to next industry.")
                        break
                
                found = self.search_companies(keyword, industry_category)
                total_found += found
            
            # Check global limits
            if self.request_count >= self.max_requests:
                break
            
            if self.max_total_companies and len(self.results) >= self.max_total_companies:
                break
        
        print(f"\n{'='*60}")
        print(f"SEARCH COMPLETE - {mode_label}")
        print(f"{'='*60}")
        print(f"Total companies WITH balance sheet: {len(self.results)}")
        print(f"Total companies checked: {self.companies_checked}")
        print(f"  ✓ With balance sheet: {self.companies_with_balance_sheet} (SAVED)")
        print(f"  → With financials only: {self.companies_without_balance_sheet} (rejected)")
        print(f"  ✗ No financials: {self.companies_without_financials} (rejected)")
        
        if self.companies_checked > 0:
            success_rate = (self.companies_with_balance_sheet / self.companies_checked) * 100
            print(f"Balance sheet success rate: {success_rate:.1f}%")
        
        print(f"API requests used: {self.request_count}/{self.max_requests}")
        print(f"Errors encountered: {len(self.errors)}")
        
        if self.mode == "test":
            print(f"\nBreakdown by industry:")
            for industry, count in self.industry_counts.items():
                print(f"  {industry}: {count} companies with balance sheet")
        
        print(f"{'='*60}\n")
        
        # Final checkpoint
        self.save_checkpoint()
        
        return self.results
    
    def save_to_csv(self, filename=None):
        """
        Save results to CSV file with timestamp
        """
        if not self.results:
            print("No results to save.")
            return
        
        # Generate filename with timestamp if not provided
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            mode_suffix = "TEST" if self.mode == "test" else "PROD"
            filename = f"boomer_businesses_v2.1_{mode_suffix}_{timestamp}.csv"
        
        # Get all unique keys from results
        fieldnames = list(self.results[0].keys())
        
        try:
            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.results)
            
            print(f"✓ Results saved to: {filename}")
            print(f"✓ Total records: {len(self.results)}")
            
            return filename
            
        except Exception as e:
            print(f"✗ Error saving CSV: {str(e)}")
            return None
    
    def save_summary(self, filename=None):
        """
        Save a summary of the search results with timestamp
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            mode_suffix = "TEST" if self.mode == "test" else "PROD"
            filename = f"search_summary_v2.1_{mode_suffix}_{timestamp}.txt"
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                mode_label = "TEST MODE" if self.mode == "test" else "PRODUCTION MODE"
                
                f.write("="*60 + "\n")
                f.write(f"BOOMER BUSINESS FINDER V2.1 - SEARCH SUMMARY ({mode_label})\n")
                f.write("="*60 + "\n\n")
                f.write(f"Search Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Mode: {mode_label}\n")
                f.write(f"Filter: ONLY companies WITH balance sheet data\n")
                f.write(f"        (current_assets OR fixed_assets arrays)\n\n")
                
                f.write(f"RESULTS:\n")
                f.write("-"*40 + "\n")
                f.write(f"Total Companies WITH Balance Sheet: {len(self.results)}\n")
                f.write(f"Total Companies Checked: {self.companies_checked}\n")
                f.write(f"  ✓ With Balance Sheet: {self.companies_with_balance_sheet} (SAVED)\n")
                f.write(f"  → With Financials Only: {self.companies_without_balance_sheet} (rejected)\n")
                f.write(f"  ✗ No Financials: {self.companies_without_financials} (rejected)\n")
                
                if self.companies_checked > 0:
                    success_rate = (self.companies_with_balance_sheet / self.companies_checked) * 100
                    f.write(f"  Balance Sheet Success Rate: {success_rate:.1f}%\n")
                
                f.write(f"\nAPI Requests Used: {self.request_count}/{self.max_requests}\n")
                f.write(f"Errors Encountered: {len(self.errors)}\n\n")
                
                # Breakdown by industry
                f.write("BREAKDOWN BY INDUSTRY:\n")
                f.write("-"*40 + "\n")
                
                for industry, count in sorted(self.industry_counts.items()):
                    f.write(f"{industry}: {count} companies with balance sheet\n")
                
                if self.mode == "test":
                    f.write("\nTEST MODE TARGETS:\n")
                    f.write(f"  Target per industry: {self.target_per_industry}\n")
                    f.write(f"  Total target: {self.max_total_companies}\n")
                
                f.write("\n" + "="*60 + "\n")
            
            print(f"✓ Summary saved to: {filename}")
            return filename
            
        except Exception as e:
            print(f"✗ Error saving summary: {str(e)}")
            return None
    
    def save_error_log(self, filename=None):
        """
        Save error log to file
        """
        if not self.errors:
            print("No errors to log.")
            return None
        
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            mode_suffix = "TEST" if self.mode == "test" else "PROD"
            filename = f"error_log_v2.1_{mode_suffix}_{timestamp}.json"
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(self.errors, f, indent=2)
            
            print(f"✓ Error log saved to: {filename}")
            print(f"✓ Total errors logged: {len(self.errors)}")
            return filename
            
        except Exception as e:
            print(f"✗ Error saving error log: {str(e)}")
            return None


def main():
    """
    Main execution function
    """
    print("\n" + "="*60)
    print("🎯 BOOMER BUSINESS FINDER V2.1 - UK Edition")
    print("="*60)
    print("Target: Retiring business owners (60-70 years old)")
    print("Focus: Cash-flowing businesses in boring industries")
    print("Filter: ONLY companies WITH balance sheet data")
    print("        (current_assets OR fixed_assets)")
    print("="*60 + "\n")
    
    # Step 1: Get API token
    print("STEP 1: API Authentication")
    print("-" * 40)
    api_token = input("Enter your OpenCorporates API token: ").strip()
    
    if not api_token:
        print("\n❌ No token provided. Exiting.")
        print("\nTo get a FREE API token:")
        print("  Visit: https://opencorporates.com/api_accounts/new")
        return
    
    print("✓ API token received\n")
    
    # Step 2: Check for checkpoint
    print("STEP 2: Check for Previous Session")
    print("-" * 40)
    checkpoint_files = [f for f in os.listdir('.') if f.startswith('checkpoint_') and f.endswith('.json')]
    
    if checkpoint_files:
        print(f"Found {len(checkpoint_files)} checkpoint file(s):")
        for i, cf in enumerate(checkpoint_files, 1):
            print(f"  {i}. {cf}")
        print(f"  {len(checkpoint_files) + 1}. Start fresh (no checkpoint)")
        
        choice = input(f"\nLoad checkpoint? (1-{len(checkpoint_files) + 1}): ").strip()
        
        try:
            choice_num = int(choice)
            if 1 <= choice_num <= len(checkpoint_files):
                checkpoint_file = checkpoint_files[choice_num - 1]
                use_checkpoint = True
            else:
                use_checkpoint = False
                checkpoint_file = None
        except:
            use_checkpoint = False
            checkpoint_file = None
    else:
        print("No previous checkpoints found.")
        use_checkpoint = False
        checkpoint_file = None
    
    print()
    
    # Step 3: Select mode
    print("STEP 3: Select Mode")
    print("-" * 40)
    print("1. TEST MODE     - 50 companies with balance sheet (25 Accounting + 25 Vending/ATM)")
    print("2. PRODUCTION MODE - All 4 industries, no limits")
    print()
    
    mode_choice = input("Enter your choice (1 or 2): ").strip()
    
    if mode_choice == "1":
        mode = "test"
        print("✓ TEST MODE selected\n")
    elif mode_choice == "2":
        mode = "production"
        print("✓ PRODUCTION MODE selected\n")
    else:
        print("\n❌ Invalid choice. Defaulting to TEST MODE.\n")
        mode = "test"
    
    # Step 4: Initialize and run
    print("STEP 4: Running Search")
    print("-" * 40)
    print("⚠️ NOTE: This will take longer due to detailed company fetches")
    print("⚠️ Expected: ~2-5 seconds per company detail check")
    print("⚠️ NEW: Now filtering for balance sheet data specifically")
    print()
    
    try:
        finder = BoomerBusinessFinder(api_token=api_token, mode=mode)
        
        # Load checkpoint if selected
        if use_checkpoint and checkpoint_file:
            if finder.load_checkpoint(checkpoint_file):
                print(f"✓ Resuming from checkpoint\n")
        
    except ValueError as e:
        print(f"\n❌ Error: {e}")
        return
    
    # Run the search
    results = finder.run_search()
    
    # Save results
    if results:
        csv_file = finder.save_to_csv()
        summary_file = finder.save_summary()
        error_file = finder.save_error_log()
        
        # NEW: Run financial analysis automatically
        print("\n" + "="*60)
        print("🔍 RUNNING FINANCIAL ANALYSIS")
        print("="*60)
        
        try:
            from financial_analyzer import analyze_results
            analysis_files = analyze_results(csv_file)
            
            if analysis_files:
                print("\n✓ FINANCIAL ANALYSIS COMPLETE!")
                print(f"  Enhanced CSV: {analysis_files['enhanced_csv']}")
                print(f"  Top 20 Prospects: {analysis_files['top_prospects_csv']}")
                print(f"  Summary Report: {analysis_files['summary_report']}")
        except Exception as e:
            print(f"\n⚠ Financial analysis failed: {str(e)}")
            print("  Continuing without analysis...")
        
        print("\n" + "="*60)
        print("✓ MISSION COMPLETE!")
        print("="*60)
        print(f"✓ Found {len(results)} companies WITH balance sheet data")
        print(f"✓ Checked {finder.companies_checked} total companies")
        print(f"✓ Balance sheet success rate: {(finder.companies_with_balance_sheet / finder.companies_checked * 100):.1f}%")
        print(f"✓ Files saved:")
        print(f"    • {csv_file}")
        print(f"    • {summary_file}")
        if error_file:
            print(f"    • {error_file}")
        print(f"    • {finder.checkpoint_file}")
        
        if mode == "test":
            print(f"\n💡 Next Steps:")
            print(f"    1. Review the {len(results)} companies with balance sheet data")
            print(f"    2. Analyze current_assets & fixed_assets in Financial_Data column")
            print(f"    3. Run PRODUCTION MODE for complete dataset")
        else:
            print(f"\n💡 Next Steps:")
            print(f"    1. Analyze balance sheet data to identify best targets")
            print(f"    2. Calculate asset ratios and trends")
            print(f"    3. Scrape additional contact details")
            print(f"    4. Begin outreach campaign")
        
        print("="*60 + "\n")
    else:
        print("\n⚠ No results found. Consider:")
        print("  - Checking your API token")
        print("  - Verifying your network connection")
        print("  - Note: Many companies may lack balance sheet data")
        print("  - Trying again later or adjusting search parameters")


if __name__ == "__main__":
    main()