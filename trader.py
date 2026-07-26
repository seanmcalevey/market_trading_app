import requests
import json
import sys
import os
import time
import datetime
from urllib.parse import urlparse
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
import base64

# Kalshi API configuration
# Use external-api.kalshi.com for production or external-api.demo.kalshi.co for demo
KALSHI_API_URL = "https://external-api.kalshi.com/trade-api/v2"
API_KEY_ID = "cc9f7a70-b6f3-482c-b573-8a77a53557eb"
PRIVATE_KEY_PATH = os.path.expanduser("~/.ssh/id_rsa_kalshi.pub")
BUFFER = .1
TOTAL_MAX = 100
UNIT_SIZE = 1

class KalshiMarketTrading:

    def __init__(self, api_key_id, private_key_path, kalshi_api_url=KALSHI_API_URL):
        """Initialize Kalshi trader with API key authentication"""
        self.api_key_id = api_key_id
        self.private_key_path = private_key_path
        self.token = None
        self.session = requests.Session()
        self.kalshi_api_url = kalshi_api_url
        self.private_key = self._load_private_key()
        
    def _load_private_key(self):
        """Load RSA private key from file"""
        try:
            with open(self.private_key_path, 'rb') as f:
                key = serialization.load_pem_private_key(
                    f.read(),
                    password=None,
                    backend=default_backend()
                )
            return key
        except Exception as e:
            print(f"Failed to load private key: {e}")
            return None
    
    def _sign_request(self, method, path):
        """Create RSA-PSS signature for API request"""
        try:
            # Use current timestamp in milliseconds
            timestamp = str(int(datetime.datetime.now().timestamp() * 1000))
            
            # Signing requires the full URL path from root
            sign_path = urlparse(self.kalshi_api_url + path).path
            
            # Create the message to sign: timestamp + method + path
            message = f"{timestamp}{method}{sign_path}".encode('utf-8')
            
            # Sign with RSA-PSS
            signature = self.private_key.sign(
                message,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH
                ),
                hashes.SHA256()
            )
            
            # Return base64 encoded signature and timestamp
            return base64.b64encode(signature).decode('utf-8'), timestamp
        except Exception as e:
            print(f"Failed to sign request: {e}")
            return None, None
        
    def _make_authenticated_request(self, method, path, json_data=None):
        """Make an authenticated request with API key signature"""
        try:
            signature, timestamp = self._sign_request(method, path)
            
            if not signature or not timestamp:
                print("Failed to create signature")
                return None
            
            headers = {
                "KALSHI-ACCESS-KEY": self.api_key_id,
                "KALSHI-ACCESS-SIGNATURE": signature,
                "KALSHI-ACCESS-TIMESTAMP": timestamp,
                "Content-Type": "application/json"
            }
            
            url = f"{self.kalshi_api_url}{path}"
            
            if method == "GET":
                response = self.session.get(url, headers=headers, timeout=10)
            elif method == "POST":
                response = self.session.post(url, headers=headers, json=json_data, timeout=10)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Request failed: {e}")
            return None
    
    def get_market(self, ticker=None):
        if ticker == None:
            ticker = input('Ticker: ')

        return self._make_authenticated_request("GET", f"/markets/{ticker}")
    
    def buy_shares(self, ticker, quantity, price_limit):
        return self._make_authenticated_request("POST", "/orders", {
            "ticker": ticker,
            "side": "BUY",
            "quantity": quantity,
            "action": "create",
            "type": "limit",
            "limit_price": price_limit
        })
    
    def sell_shares(self, ticker, quantity, price_limit):
        return self._make_authenticated_request("POST", "/orders", {
            "ticker": ticker,
            "side": "SELL",
            "quantity": quantity,
            "action": "create",
            "type": "limit",
            "limit_price": price_limit
        })
    
    def get_portfolio(self):
        return self._make_authenticated_request("GET", "/portfolio/balance")







if __name__ == "__main__":

    team = "Cleveland Guardians"
    with open('mlb_teams.json', 'r') as f:
        tickers = json.load(f)
        kalshi_ticker = tickers.get(team)

    with open('x_value.json', 'r') as f:
        team_values = json.load(f)
        expected_cents_value = team_values.get(team)
        # Convert to float, default to 0 if empty or missing
        expected_cents = float(expected_cents_value) if expected_cents_value else 0

    # "KXMLB-26-PHI"


    # Initialize trader with API key
    trader = KalshiMarketTrading(api_key_id=API_KEY_ID,
                                 private_key_path=PRIVATE_KEY_PATH,
                                 kalshi_api_url=KALSHI_API_URL)
    
    # Get portfolio
    portfolio = trader.get_portfolio()
    print("Portfolio: ")
    print(portfolio)

    # Expected range
    sell_target = expected_cents * (1 + BUFFER)
    buy_target = expected_cents * (1 - BUFFER)

    print(f"Team: {team}")
    print(f"Buy target: {buy_target} cents")
    print(f"Sell target: {sell_target} cents")
    
    # Get market info
    count = 0
    amount_max = 10
    while count < amount_max:

        market = trader.get_market(kalshi_ticker)

        # print(f"Market: {market}")
        ask_price = round(float(market['market']['yes_ask_dollars']) * 100, 2)
        ask_amount = market['market']['yes_ask_size_fp']
        bid_price = round(float(market['market']['yes_bid_dollars']) * 100, 2)
        bid_amount = market['market']['yes_bid_size_fp']

        print(f"Market ask (price, amount): {ask_price} cents, {ask_amount} shares")
        print(f"Market bid (price, amount): {bid_price} cents, {bid_amount} shares")

        if ask_price < buy_target:
            buy_result = trader.buy_shares(kalshi_ticker, quantity=UNIT_SIZE, price_limit=ask_price)
            # print(f"Buy order: {buy_result}")
            print(f'Bought {UNIT_SIZE} shares at {ask_price} cents')
            # print(f"Ask price {ask_price} cents is below buy target {buy_target} cents")

        # if bid_price > sell_target:
        # #     sell_result = trader.sell_shares(kalshi_ticker, quantity=UNIT_SIZE, price_limit=bid_price)
        # #     print(f"Sell order: {sell_result}")
        #     print(f"Bid price {bid_price} cents is above sell target {sell_target} cents")

        time.sleep(30)
        count += 1

    sys.exit()
    
    # # Buy shares (limit price example: $0.50)

    
    # # Sell shares (limit price example: $0.70)
    # sell_result = trader.sell_shares(kalshi_ticker, quantity=5, price_limit=0.70)
    # print(f"Sell order: {sell_result}")
    
    # Get portfolio
    portfolio = trader.get_portfolio()
    print(f"Portfolio: {portfolio}")
