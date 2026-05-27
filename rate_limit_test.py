import sys

if not ((3, 8) <= sys.version_info < (3, 13)):
    raise RuntimeError(
        f"Python 3.8–3.12 required (webull-openapi-python-sdk constraint). "
        f"You are running {sys.version}. "
        "Switch versions with: py -3.12 rate_limit_test.py"
    )

import json
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable

from dotenv import load_dotenv
from webull.core.client import ApiClient
from webull.data.data_client import DataClient
from webull.trade.trade_client import TradeClient

load_dotenv()

APP_KEY = os.environ["WEBULL_APP_KEY"]
APP_SECRET = os.environ["WEBULL_APP_SECRET"]
ACCOUNT_ID = os.environ["WEBULL_ACCOUNT_ID"]
REGION = os.environ.get("WEBULL_REGION", "us")
ENV = os.environ.get("WEBULL_ENV", "uat")

TOKEN_DIR = os.environ.get("WEBULL_TOKEN_DIR", "conf")
Path(TOKEN_DIR).mkdir(parents=True, exist_ok=True)

# Documented rate limits from API docs
RATE_LIMITS = {
    "account_list": (10, 30),        # 10/30s
    "account_balance": (2, 2),       # 2/2s
    "account_positions": (2, 2),     # 2/2s
    "order_preview": (150, 10),      # 150/10s
    "order_place": (600, 60),        # 600/60s
    "order_history": (2, 2),         # 2/2s
    "order_open": (2, 2),            # 2/2s
    "order_detail": (2, 2),          # 2/2s
}


@dataclass
class RateLimitResult:
    endpoint: str
    documented_limit: str
    requests_made: int
    time_window: float
    http_statuses: dict = field(default_factory=lambda: defaultdict(int))
    first_429: int | None = None  # Request number when first 429 received
    requests_per_sec: float = 0.0


def _build_clients() -> tuple[TradeClient, DataClient]:
    api_client = ApiClient(APP_KEY, APP_SECRET, REGION)
    api_client.set_token_dir(TOKEN_DIR)

    # Point at UAT (paper trading) endpoint when not in prod
    if ENV != "prod":
        uat_host = "us-openapi-alb.uat.webullbroker.com"
        api_client.add_endpoint(REGION, uat_host)

    trade = TradeClient(api_client)
    data = DataClient(api_client)
    return trade, data


def _test_endpoint(
    trade: TradeClient,
    endpoint_name: str,
    api_call: Callable,
    num_requests: int = 200,
) -> RateLimitResult:
    """Test an endpoint with rapid requests to find rate limit."""
    
    doc_limit = RATE_LIMITS.get(endpoint_name, (0, 0))
    doc_limit_str = f"{doc_limit[0]}/{doc_limit[1]}s"
    
    print(f"\n{'='*60}")
    print(f"Testing: {endpoint_name}")
    print(f"Documented Limit: {doc_limit_str}")
    print(f"Planned Requests: {num_requests}")
    print('='*60)
    
    result = RateLimitResult(
        endpoint=endpoint_name,
        documented_limit=doc_limit_str,
        requests_made=0,
        time_window=0.0,
    )
    
    start_time = time.time()
    
    for i in range(1, num_requests + 1):
        try:
            res = api_call()
            status_code = res.status_code
            result.http_statuses[status_code] += 1
            
            # Track first rate limit
            if status_code == 429 and result.first_429 is None:
                result.first_429 = i
                print(f"  ⚠️  Rate limit (429) hit at request #{i}")
            
            # Stop early if we hit rate limit
            if status_code == 429:
                time.sleep(0.5)  # Back off
            else:
                # Small delay between requests to allow processing
                time.sleep(0.01)
            
            if i % 10 == 0:
                elapsed = time.time() - start_time
                rate = i / elapsed
                print(f"  {i} requests completed ({rate:.1f} req/s) - Status: {status_code}")
            
            result.requests_made = i
            
        except Exception as e:
            print(f"  ❌ Error at request #{i}: {str(e)[:100]}")
            break
    
    result.time_window = time.time() - start_time
    result.requests_per_sec = result.requests_made / result.time_window if result.time_window > 0 else 0.0
    
    return result


def test_account_endpoints(trade: TradeClient) -> list[RateLimitResult]:
    """Test account-related endpoints."""
    results = []
    
    # Account List (10/30s)
    def account_list():
        return trade.account_v2.get_account_list()
    
    results.append(_test_endpoint(
        trade, "account_list", account_list, num_requests=50
    ))
    
    # Account Balance (2/2s)
    def account_balance():
        return trade.account_v2.get_account_balance(ACCOUNT_ID)
    
    results.append(_test_endpoint(
        trade, "account_balance", account_balance, num_requests=20
    ))
    
    # Account Positions (2/2s)
    def account_positions():
        return trade.account_v2.get_account_position(ACCOUNT_ID)
    
    results.append(_test_endpoint(
        trade, "account_positions", account_positions, num_requests=20
    ))
    
    return results


def test_order_endpoints(trade: TradeClient) -> list[RateLimitResult]:
    """Test order-related endpoints."""
    results = []
    
    # Order Preview (150/10s)
    def order_preview():
        order = {
            "client_order_id": str(uuid.uuid4()),
            "symbol": "AAPL",
            "instrument_type": "EQUITY",
            "market": "US",
            "side": "BUY",
            "order_type": "LIMIT",
            "limit_price": "180.00",
            "quantity": "1",
            "entrust_type": "QTY",
            "time_in_force": "DAY",
            "support_trading_session": "CORE",
            "combo_type": "NORMAL",
        }
        return trade.order_v2.preview_order(ACCOUNT_ID, [order])
    
    results.append(_test_endpoint(
        trade, "order_preview", order_preview, num_requests=200
    ))
    
    # Order History (2/2s)
    def order_history():
        return trade.order_v2.get_order_history(ACCOUNT_ID, 1, 10)
    
    results.append(_test_endpoint(
        trade, "order_history", order_history, num_requests=20
    ))
    
    # Open Orders (2/2s)
    def order_open():
        return trade.order_v2.get_order_open(ACCOUNT_ID)
    
    results.append(_test_endpoint(
        trade, "order_open", order_open, num_requests=20
    ))
    
    return results


def print_summary(all_results: list[RateLimitResult]):
    """Print summary of rate limit test results."""
    print(f"\n\n{'='*70}")
    print("RATE LIMIT TEST SUMMARY".center(70))
    print('='*70)
    
    headers = ["Endpoint", "Documented", "Requests", "Time (s)", "Req/s", "Status Codes", "First 429 @ Req"]
    col_widths = [20, 12, 12, 10, 10, 20, 15]
    
    print("  ".join(f"{h:<{w}}" for h, w in zip(headers, col_widths)))
    print("-" * 100)
    
    for result in all_results:
        statuses = ", ".join(f"{code}:{count}" for code, count in sorted(result.http_statuses.items()))
        first_429_str = f"#{result.first_429}" if result.first_429 else "—"
        
        row = [
            result.endpoint[:20],
            result.documented_limit,
            str(result.requests_made),
            f"{result.time_window:.2f}",
            f"{result.requests_per_sec:.1f}",
            statuses[:20],
            first_429_str,
        ]
        print("  ".join(f"{v:<{w}}" for v, w in zip(row, col_widths)))
    
    print("\nLegend:")
    print("  • Status Codes: HTTP responses (200=success, 429=rate limit, etc.)")
    print("  • First 429 @ Req: Request number when rate limit was first hit")
    print("  • Req/s: Average requests per second during test window")
    print("\nNote: Rate limits may vary based on API subscription tier and account type.")


def main():
    print("\n🚀 Webull API Rate Limit Testing Script")
    print(f"Environment: {ENV}")
    print(f"Account ID: {ACCOUNT_ID[:10]}...")
    
    trade, data = _build_clients()
    
    all_results = []
    
    # Test account endpoints
    print("\n📊 Testing Account Endpoints...")
    all_results.extend(test_account_endpoints(trade))
    
    # Test order endpoints
    print("\n📦 Testing Order Endpoints...")
    all_results.extend(test_order_endpoints(trade))
    
    # Print summary
    print_summary(all_results)
    
    # Save detailed results to JSON
    results_file = Path("rate_limit_results.json")
    with open(results_file, "w") as f:
        data = {
            "timestamp": datetime.now().isoformat(),
            "environment": ENV,
            "results": [
                {
                    "endpoint": r.endpoint,
                    "documented_limit": r.documented_limit,
                    "requests_made": r.requests_made,
                    "time_window": r.time_window,
                    "requests_per_sec": r.requests_per_sec,
                    "http_statuses": dict(r.http_statuses),
                    "first_429_at_request": r.first_429,
                }
                for r in all_results
            ]
        }
        json.dump(data, f, indent=2)
    
    print(f"\n✅ Results saved to {results_file}")


if __name__ == "__main__":
    main()
