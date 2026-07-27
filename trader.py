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
import psycopg2

# Kalshi API configuration
# Use external-api.kalshi.com for production or external-api.demo.kalshi.co for demo
KALSHI_API_URL = "https://external-api.kalshi.com/trade-api/v2"
API_KEY_ID = "cc9f7a70-b6f3-482c-b573-8a77a53557eb"
DB_URL = os.environ.get("DATABASE_URL")
PRIVATE_KEY_PATH = 'private_key.pem'
# PRIVATE_KEY_PATH = os.path.expanduser("~/.ssh/id_rsa_kalshi.pub")
BUFFER = .2
TOTAL_NET_PURCHASE_MAX = 150
UNIT_SIZE = 1
TEAM_LIST = ['Cleveland Guardians', 'Tampa Bay Rays', 'Minnesota Twins', 'Seattle Mariners', 'Texas Rangers', 'Detroit Tigers', 
            'Chicago Cubs', 'Pittsburgh Pirates']
MOMENTUM = True
MOMENTUM_CAP = 20


class KalshiMarketTrading:

    def __init__(self, api_key_id, private_key_path, kalshi_api_url=KALSHI_API_URL):
        """Initialize Kalshi trader with API key authentication"""
        self.api_key_id = api_key_id
        self.private_key_path = private_key_path
        self.token = None
        self.session = requests.Session()
        self.kalshi_api_url = kalshi_api_url
        self.private_key = self._load_private_key()
        self.team_buy_sell_count = dict()
        self.team_last_buy = dict()
        self.team_last_sell = dict()
        self.first_write = True
        
        
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

    def calculate_momentum_quantity(self, quantity, price_limit, team, buy_order=True):
        if buy_order:
            price, amount = self.team_last_buy.get(team, (0, 0))
        else:
            price, amount = self.team_last_sell.get(team, (0, 0))
        
        if price == price_limit:
            cap_amount = quantity * MOMENTUM_CAP
            quantity = amount + quantity if amount <= cap_amount else cap_amount
            print(f'Momentum used!!! Momentum purchase for {team}: {quantity} @ {price}', flush=True)
            time.sleep(2)  # Simulate delay

        else:
            if buy_order:
                self.team_last_buy[team] = (price_limit, quantity)
            else:
                self.team_last_sell[team] = (price_limit, quantity)
            
    def buy_shares(self, ticker, team, quantity, price_limit):

        # Adds previous buy amounts to the current purchase
        if MOMENTUM:
            quantity = self.calculate_momentum_quantity(quantity, price_limit, team=team, buy_order=True)

        # Convert price from cents to dollars
        price_dollars = price_limit / 100
        return self._make_authenticated_request("POST", "/portfolio/events/orders", {
            "ticker": ticker,
            "side": "bid",
            "count": str(int(quantity)),
            "price": f"{price_dollars:.3f}",
            "time_in_force": "good_till_canceled",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False,
            "cancel_order_on_pause": False,
            "reduce_only": False,
            "subaccount": 0,
            "exchange_index": 0
        })
    
    def sell_shares(self, ticker, team, quantity, price_limit):

        # Adds previous sell amounts to the current sell order
        if MOMENTUM:
            quantity = self.calculate_momentum_quantity(quantity, price_limit, team=team, buy_order=False)

        # Convert price from cents to dollars
        price_dollars = price_limit / 100
        return self._make_authenticated_request("POST", "/portfolio/events/orders", {
            "ticker": ticker,
            "side": "ask",
            "count": str(int(quantity)),
            "price": f"{price_dollars:.3f}",
            "time_in_force": "good_till_canceled",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False,
            "cancel_order_on_pause": False,
            "reduce_only": False,
            "subaccount": 0,
            "exchange_index": 0
        })
    
    def get_portfolio(self):
        return self._make_authenticated_request("GET", "/portfolio/balance")

    

    def get_current_team_value(self, team):
        """Reads a variable value from the database"""
        try:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            # Example query: get a threshold or setting from a configuration table
            cur.execute("SELECT value FROM team_values WHERE team = %s;", (team,))
            row = cur.fetchone()
            value = row[0] if row and len(row) > 0 else None
            cur.close()
            conn.close()
            return value
            
        except Exception as e:
            print(f"Database read error: {e}")
            return None

    def get_buy_sell_count(self, team):
        buy_sell_counts = self.team_buy_sell_count.get(team)
        if buy_sell_counts:
            buy_count = buy_sell_counts.get('buy', 0)
            sell_count = buy_sell_counts.get('sell', 0)
        else:
            buy_count = 0
            sell_count = 0
            self.team_buy_sell_count[team] = {'buy': 0, 'sell': 0}

        return (buy_count, sell_count)

    def update_buy_sell_count(self, team, buy_count=None, sell_count=None):
        if self.team_buy_sell_count.get(team):
            if buy_count:
                self.team_buy_sell_count[team]['buy'] = buy_count

            if sell_count:
                self.team_buy_sell_count[team]['sell'] = sell_count
        else:
            buy_count = buy_count if buy_count else 0
            sell_count = sell_count if sell_count else 0
            self.team_buy_sell_count[team] = {'buy': buy_count, 'sell': sell_count}


    def get_net_trade_table_as_json(self):
        net_table_dict = {}
        try:
            print("\nConnecting to the database to get net trade table data...")
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            # Select all rows from team_values
            cur.execute("SELECT * FROM net_trades;")
            rows = cur.fetchall()
            for row in rows:
                # Columns: team, date, net_amount, net_shares, updated_at
                team = row[0]
                net_amount = float(row[2]) if len(row) > 2 else 0
                net_shares = int(row[3]) if len(row) > 3 else 0
                net_table_dict[team] = (net_amount, net_shares)

            cur.close()
            conn.close()
            print('Fetched net trade data from net trade table')

            return net_table_dict

        except Exception as e:
            print(f"An error occurred while viewing table: {e}")

    
    def write_to_net_trade_table(self, net_session_team_purchases: dict):
        """
        Creates the table if missing, and safely inserts or updates 
        a Python dictionary using PostgreSQL JSONB format.
        """
        try:
            # Connect to your Render Postgres Database
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()

            # Create table on first write
            if self.first_write:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS net_trades (
                        team VARCHAR(100) PRIMARY KEY,
                        date VARCHAR(25),
                        net_amount DECIMAL(10, 3) NOT NULL,
                        net_shares INTEGER NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)

                self.first_write = False

            today_date_str = datetime.date.today().strftime("%m/%d")
            for team in TEAM_LIST:
                values = net_session_team_purchases.get(team, (0, 0))
                try:
                    net_amount = values[0]
                    net_shares = values[1]
                except Exception:
                    net_amount, net_shares = 0, 0

                cur.execute(
                    """
                    INSERT INTO net_trades (team, date, net_amount, net_shares)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (team) DO UPDATE 
                    SET net_amount = EXCLUDED.net_amount, net_shares = EXCLUDED.net_shares;
                    """,
                    (team, today_date_str, net_amount, net_shares)
                )

            # Commit changes to make them permanent
            conn.commit()
            cur.close()
            conn.close()
            print(f"Successfully saved net trade data", flush=True)

        except Exception as e:
            print(f"Database operation failed: {e}", flush=True)
            
        finally:
            # Always close connections to avoid leaking resources
            if 'cur' in locals(): cur.close()
            if 'conn' in locals(): conn.close()



if __name__ == "__main__":

    # team = "Cleveland Guardians"
    with open('mlb_teams.json', 'r') as f:
        kalshi_tickers = json.load(f)


    # Initialize trader with API key
    trader = KalshiMarketTrading(api_key_id=API_KEY_ID,
                                 private_key_path=PRIVATE_KEY_PATH,
                                 kalshi_api_url=KALSHI_API_URL)
    
    # Get portfolio
    portfolio = trader.get_portfolio()
    print("Portfolio: ")
    print(portfolio)

    # Buy/sell counts
    team_buy_sell_count = {}
    
    # Get market info
    unsuccessful_attempts = 0
    max_unsuccessful = 15
    count = 0
    amount_max = 10000
    net_session_purchases = 0
    net_session_team_purchases = trader.get_net_trade_table_as_json()
    if net_session_team_purchases == None:
        net_session_team_purchases = {t: (0, 0) for t in TEAM_LIST}
    while count < amount_max:
        if net_session_purchases >= TOTAL_NET_PURCHASE_MAX:
            print(f'Reached max session purchases of {TOTAL_NET_PURCHASE_MAX}', flush=True)
            break

        for team in TEAM_LIST:

            kalshi_ticker = kalshi_tickers.get(team)
            market = trader.get_market(kalshi_ticker)

            expected_cents_value = trader.get_current_team_value(team)
            expected_cents = float(expected_cents_value) if expected_cents_value else 0

            # Expected range
            sell_target = expected_cents * (1 + BUFFER)
            buy_target = expected_cents * (1 - BUFFER)

            ask_price = round(float(market['market']['yes_ask_dollars']) * 100, 2)
            ask_amount = market['market']['yes_ask_size_fp']
            bid_price = round(float(market['market']['yes_bid_dollars']) * 100, 2)
            bid_amount = market['market']['yes_bid_size_fp']

            # Status every x runs
            every_x_runs = 10
            if count % every_x_runs == 0:
                print(f"Team: {team}", flush=True)
                print(f"Buy target: {round(buy_target, 2)} cents", flush=True)
                print(f"Sell target: {round(sell_target, 2)} cents", flush=True)
                print(f"Market ask (price, amount): {round(ask_price, 2)} cents, {ask_amount} shares", flush=True)
                print(f"Market bid (price, amount): {round(bid_price, 2)} cents, {bid_amount} shares", flush=True)
    


            # Get buy-sell counts
            buy_count, sell_count = trader.get_buy_sell_count(team)
            # print(f'{team} buy count: {buy_count}', flush=True)
            # print(f'{team} sell count: {sell_count}', flush=True)
            
            
            # BUY SIDE
            if ask_price < buy_target:
                try:
                    buy_result = trader.buy_shares(kalshi_ticker, team, quantity=UNIT_SIZE, price_limit=ask_price)
                    total_purchase = UNIT_SIZE * ask_price
                    all_time_team_net_purchase, all_time_net_shares_purchase = net_session_team_purchases[team]
                    all_time_team_net_purchase += total_purchase
                    all_time_net_shares_purchase += UNIT_SIZE
                    net_session_team_purchases[team] = (all_time_team_net_purchase, all_time_net_shares_purchase)
                    all_time_purchase = round((buy_count + total_purchase) / 100, 2)
                    net_session_purchases += all_time_purchase
                    # print(f"Buy order: {buy_result}")
                    print(f'\n!!! Bought {UNIT_SIZE} shares for {team} at {ask_price} cents', flush=True)
                    print(f'!!! {team} session buy amount: ${all_time_purchase}!!!...\n', flush=True)
                            
                    # print(f"Ask price {ask_price} cents is below buy target {buy_target} cents")
                    buy_count += total_purchase
                    trader.update_buy_sell_count(team, buy_count=buy_count)
                    
                except Exception as e:
                    print(f'Unsuccessful buy attempt: {e}', flush=True)
                    unsuccessful_attempts += 1
                    if unsuccessful_attempts > max_unsuccessful:
                        sys.exit()


            

            # SELL SIDE
            if bid_price > sell_target:

                # Ensures we don't sell more than we buy
                if buy_count > sell_count:
                    try:
                        sell_result = trader.sell_shares(kalshi_ticker, team, quantity=UNIT_SIZE, price_limit=bid_price)
                        # print(f"Sell order: {sell_result}")
                        total_sell = UNIT_SIZE * bid_price
                        all_time_team_net_purchase, all_time_net_shares_purchase = net_session_team_purchases[team]
                        all_time_team_net_purchase -= total_sell
                        all_time_net_shares_purchase -= UNIT_SIZE
                        net_session_team_purchases[team] = (all_time_team_net_purchase, all_time_net_shares_purchase)
                        all_time_sell = round((sell_count + total_sell) / 100, 2)
                        net_session_purchases -= all_time_sell
                        print(f'\n...!!! Sold {UNIT_SIZE} shares for {team} at {bid_price} cents', flush=True)
                        print(f'!!! {team} session sell amount: ${all_time_sell}!!!...\n', flush=True)
                        # print(f"Bid price {bid_price} cents is above sell target {sell_target} cents")
                        
                        total_purchase = UNIT_SIZE * bid_price
                        sell_count += total_purchase
                        trader.update_buy_sell_count(team, sell_count=sell_count)
                        
                            
                    except Exception as e:
                        print(f'Unsuccessful buy attempt: {e}', flush=True)
                        unsuccessful_attempts += 1
                        if unsuccessful_attempts > max_unsuccessful:
                            sys.exit()

        trader.write_to_net_trade_table(net_session_team_purchases)
        time.sleep(20)
        count += 1


    sys.exit()
    
    # # Buy shares (limit price example: $0.50)

    
    # # Sell shares (limit price example: $0.70)
    # sell_result = trader.sell_shares(kalshi_ticker, quantity=5, price_limit=0.70)
    # print(f"Sell order: {sell_result}")
    
    # Get portfolio
    portfolio = trader.get_portfolio()
    print(f"Portfolio: {portfolio}")
