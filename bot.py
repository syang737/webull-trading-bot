"""
Webull trading bot - market data, test orders, and account balance.

Requires credentials in .env (copy from .env.example):
  WEBULL_APP_KEY, WEBULL_APP_SECRET, WEBULL_ACCOUNT_ID

Authentication: on first run the SDK will trigger a 2FA push to your Webull
mobile app. Approve it, and a token is cached at conf/token.txt for future runs.
"""

import sys

if not ((3, 8) <= sys.version_info < (3, 13)):
    raise RuntimeError(
        f"Python 3.8–3.12 required (webull-openapi-python-sdk constraint). "
        f"You are running {sys.version}. "
        "Switch versions with: py -3.12 bot.py"
    )

import json
import os
import uuid
from pathlib import Path

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

WATCHLIST = ["AAPL", "TSLA", "MSFT"]

TOKEN_DIR = os.environ.get("WEBULL_TOKEN_DIR", "conf")
Path(TOKEN_DIR).mkdir(parents=True, exist_ok=True)


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


def _ok(res, label: str) -> dict | None:
    if res.status_code == 200:
        return res.json()
    print(f"[ERROR] {label} — HTTP {res.status_code}: {res.text}")
    return None


# ---------------------------------------------------------------------------
# Feature 1: market data
# ---------------------------------------------------------------------------

def fetch_market_data(data: DataClient, symbols: list[str]) -> None:
    print("\n=== Market Data ===")
    res = data.market_data.get_snapshot(symbols)
    payload = _ok(res, "get_snapshot")
    if payload is None:
        return
    quotes = payload if isinstance(payload, list) else payload.get("data", [payload])
    for q in quotes:
        symbol = q.get("symbol", q.get("ticker", "?"))
        last = q.get("close", q.get("last", q.get("lastPrice", "N/A")))
        bid = q.get("bid", q.get("bidPrice", "N/A"))
        ask = q.get("ask", q.get("askPrice", "N/A"))
        volume = q.get("volume", "N/A")
        print(f"  {symbol:6s}  last={last}  bid={bid}  ask={ask}  vol={volume}")


# ---------------------------------------------------------------------------
# Feature 2: test buy and sell orders
# ---------------------------------------------------------------------------

def _place_stock_order(
    trade: TradeClient,
    symbol: str,
    side: str,          # "BUY" or "SELL"
    qty: int,
    order_type: str = "MARKET",
    limit_price: str | None = None,
) -> str | None:
    """Place an order and return the server order ID on success."""
    client_order_id = str(uuid.uuid4())
    body = {
        "account_id": ACCOUNT_ID,
        "client_order_id": client_order_id,
        "symbol": symbol,
        "instrument_type": "EQUITY",
        "market": "US",
        "side": side,
        "order_type": order_type,
        "quantity": str(qty),
        "entrust_type": "QTY",
        "time_in_force": "DAY",
        "support_trading_session": "CORE",
        "combo_type": "NORMAL",
    }
    if order_type == "LIMIT" and limit_price:
        body["limit_price"] = limit_price

    res = trade.stock_order.place_order(ACCOUNT_ID, body)
    payload = _ok(res, f"place_order {side} {symbol}")
    if payload is None:
        return None

    order_id = payload.get("order_id") or payload.get("orderId")
    print(f"  {side} {qty}x {symbol} — client_order_id={client_order_id}  order_id={order_id}")
    return order_id


def place_test_orders(trade: TradeClient) -> None:
    print("\n=== Test Orders ===")
    # Test buy: 1 share of AAPL at market
    buy_id = _place_stock_order(trade, "AAPL", "BUY", qty=1)
    print(f"  Buy order id: {buy_id}")

    # Test sell: 1 share of TSLA at market
    # NOTE: this will fail if you have no position — that's expected in paper trading.
    sell_id = _place_stock_order(trade, "TSLA", "SELL", qty=1)
    print(f"  Sell order id: {sell_id}")


# ---------------------------------------------------------------------------
# Feature 3: account balance
# ---------------------------------------------------------------------------

def fetch_account_balance(trade: TradeClient) -> None:
    print("\n=== Account Balance ===")
    res = trade.account_v2.get_account_balance(ACCOUNT_ID, currencies=["USD"])
    payload = _ok(res, "get_account_balance")
    if payload is None:
        return
    balances = payload if isinstance(payload, list) else [payload]
    for entry in balances:
        currency = entry.get("currency", "USD")
        net_liq = entry.get("net_liquidation", entry.get("netLiquidation", "N/A"))
        cash = entry.get("cash_balance", entry.get("cashBalance", "N/A"))
        buying_power = entry.get("buying_power", entry.get("buyingPower", "N/A"))
        print(f"  [{currency}]  net_liquidation={net_liq}  cash={cash}  buying_power={buying_power}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"Webull Trading Bot  (env={ENV}, region={REGION})")
    print(f"Tickers: {WATCHLIST}")

    trade, data = _build_clients()

    fetch_market_data(data, WATCHLIST)
    place_test_orders(trade)
    fetch_account_balance(trade)

    print("\nDone.")


if __name__ == "__main__":
    main()
