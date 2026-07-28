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
BUY_BUFFER, SELL_BUFFER = .2, .12
TOTAL_NET_PURCHASE_MAX = 200
UNIT_SIZE = 1
MAKER_UNIT_SIZE, MIN_OPEN_ORDERS = 100, 25
TEAM_LIST = ['Cleveland Guardians', 'Tampa Bay Rays', 'Minnesota Twins',
             'Seattle Mariners', 'Texas Rangers', 'Detroit Tigers', 
            'Chicago Cubs', 'Pittsburgh Pirates', 'Baltimore Orioles',
            'Philadelphia Phillies', 'Houston Astros', 'Boston Red Sox', 'Tampa Bay Rays']
BUY_ONLY_TEAMS = ['Houston Astros', 'Boston Red Sox', 'Tampa Bay Rays']
SELL_ONLY_TEAMS = []
# TEAM_LIST = ['Texas Rangers']
MOMENTUM = True
MOMENTUM_CAP = 3
DEBUG = False


class KalshiMarketTrading:

    def __init__(self, api_key_id, private_key_path, kalshi_api_url=KALSHI_API_URL, debug=DEBUG):
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
        self.open_orders_by_team = {t: None for t in TEAM_LIST}
        self.first_write = True
        self.first_write_orders = True
        self.debug = debug
        
        
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
            elif method == "DELETE":
                response = self.session.delete(url, headers=headers, timeout=10)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            if not response.ok:
                print(f"Request failed: {response.status_code} {response.reason} for {method} {url}", flush=True)
                print(f"Response body: {response.text}", flush=True)
                return None

            return response.json()
        except Exception as e:
            print(f"Request failed: {e}", flush=True)
            return None

    def _parse_int(self, value):
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return None

    def _parse_float(self, value):
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def get_market(self, ticker=None):
        if ticker == None:
            ticker = input('Ticker: ')

        return self._make_authenticated_request("GET", f"/markets/{ticker}")

    def calculate_momentum_quantity(self, quantity, price_limit, team, buy_order=True):
        if buy_order:
            price, amount = self.team_last_buy.get(team, (None, 0))
        else:
            price, amount = self.team_last_sell.get(team, (None, 0))
        
        if price and (price == price_limit):
            cap_amount = quantity * MOMENTUM_CAP
            quantity = amount + quantity if amount <= cap_amount else cap_amount
            print(f'Momentum used!!! Momentum purchase for {team}: {quantity} @ {price}', flush=True)
            time.sleep(2)  # Simulate delay

        if buy_order:
            self.team_last_buy[team] = (price_limit, quantity)
        else:
            self.team_last_sell[team] = (price_limit, quantity)

        return quantity
            
    def buy_shares(self, ticker, team, quantity, price_limit, maker=False):

        # Adds previous buy amounts to the current purchase (taker orders ONLY)
        if maker == False:
            if MOMENTUM:
                quantity = self.calculate_momentum_quantity(quantity, price_limit, team=team, buy_order=True)

        buy_amount = price_limit * quantity

        type_of_order = 'maker' if maker else 'taker'
        print(f'Creating {type_of_order} sell order...', flush=True)

        # Convert price from cents to dollars
        price_dollars = price_limit / 100
        buy_result = self._make_authenticated_request("POST", "/portfolio/events/orders", {
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

        if not buy_result:
            print(f"Buy request failed or returned empty response for {team} ({ticker})", flush=True)
            return 0, 0, 0, None

        order_id = buy_result.get('order_id')
        shares_bought = self._parse_int(buy_result.get('fill_count'))
        shares_still_avail = self._parse_int(buy_result.get('remaining_count'))

        # print(f'Buy result return data: {buy_result}')

        return buy_amount, shares_bought, shares_still_avail, order_id
    
    
    def sell_shares(self, ticker, team, quantity, price_limit, maker=False):

        # Adds previous sell amounts to the current sell order
        if maker == False:
            if MOMENTUM:
                quantity = self.calculate_momentum_quantity(quantity, price_limit, team=team, buy_order=False)

        sell_amount = price_limit * quantity

        type_of_order = 'maker' if maker else 'taker'
        print(f'Creating {type_of_order} sell order...', flush=True)

        # Convert price from cents to dollars
        price_dollars = price_limit / 100
        sell_result = self._make_authenticated_request("POST", "/portfolio/events/orders", {
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

        if not sell_result:
            print(f"Sell request failed or returned empty response for {team} ({ticker})", flush=True)
            return 0, 0, 0, None

        order_id = sell_result.get('order_id')
        shares_sold = self._parse_int(sell_result.get('fill_count'))
        shares_still_avail = self._parse_int(sell_result.get('remaining_count'))

        # print(f'Sell result return data: {sell_result}')

        return sell_amount, shares_sold, shares_still_avail, order_id

    
    
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

    def get_and_update_order_status(self, order_id, team=None):
        net_transaction_amount, net_fees_amount, filled_orders = None, None, None
        if order_id:
            order_return_status = self._make_authenticated_request("GET", f"/portfolio/orders/{order_id}")
            if not order_return_status:
                return None, None, None

            order_data = order_return_status.get('order')
            if order_data:
                filled_orders = self._parse_int(order_data.get('fill_count_fp'))
                open_orders = self._parse_int(order_data.get('remaining_count_fp'))
                if open_orders is not None and open_orders < MIN_OPEN_ORDERS:
                    net_transaction_amount = self._parse_float(order_data.get('maker_fill_cost_dollars'))
                    net_fees_amount = self._parse_float(order_data.get('maker_fees_dollars'))

                    # Cancel the open order
                    cancel_request_return = self._make_authenticated_request("DELETE", f"/portfolio/events/orders/{order_id}")
                    if cancel_request_return:
                        reduced_shares = cancel_request_return.get('reduced_by')

                        # Normalize for comparison
                        try:
                            open_orders_int = int(open_orders)
                        except (TypeError, ValueError):
                            open_orders_int = None
                        try:
                            reduced_shares_int = int(reduced_shares)
                        except (TypeError, ValueError):
                            reduced_shares_int = None

                        if open_orders_int is not None and reduced_shares_int is not None and open_orders_int != reduced_shares_int:
                            print(f"\n\n !!! ERROR !!! \n\n")
                            print(f"Error: open_shares ({open_orders}) for {team or 'unknown team'} does not match total reduced_shares ({reduced_shares}) upon closure of order_id {order_id}", flush=True)
                            print(f"\n\n !!! ERROR !!! \n\n")
                        else:
                            self.debugPrint(f'Successfully cancelled open order {order_id} for team {team}...')

        return net_transaction_amount, net_fees_amount, filled_orders



    def get_net_trade_table_as_json(self):
        net_table_dict = {}
        try:
            self.debugPrint("\nConnecting to the database to get net trade table data...")
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            # Select all rows from team_values
            cur.execute("SELECT * FROM net_trades;")
            rows = cur.fetchall()
            for row in rows:
                # Columns: team, date, net_amount, net_shares
                team = row[0]
                net_amount = float(row[2]) if len(row) > 2 else 0
                net_shares = row[3] if len(row) > 3 else 0
                if net_shares == None:
                    print(f'Error processing net_shares for team {team}. NoneType...')
                    sys.exit()
                net_table_dict[team] = (net_amount, int(net_shares))

            cur.close()
            conn.close()
            self.debugPrint('Fetched net trade data from net trade table')

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

            conn.commit()
            cur.close()
            conn.close()
            self.debugPrint(f"Successfully saved net trade data")

        except Exception as e:
            print(f"Database operation failed: {e}", flush=True)
            
        finally:
            # Always close connections to avoid leaking resources
            if 'cur' in locals(): cur.close()
            if 'conn' in locals(): conn.close()


    def get_open_orders_as_json(self):
        open_orders_dict = {}
        try:
            self.debugPrint("\nConnecting to the database to get open orders data...")
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            # Select all rows from open_orders
            cur.execute("SELECT * FROM open_orders;")
            rows = cur.fetchall()
            for row in rows:
                # Columns: team, order_id
                team = row[0]
                buy_order_id = row[1]
                sell_order_id = row[2]
                open_orders_dict[team] = (buy_order_id, sell_order_id)

            cur.close()
            conn.close()
            self.debugPrint('Fetched open orders data from open orders table')

            return open_orders_dict

        except Exception as e:
            print(f"An error occurred while viewing table: {e}")

    def write_to_open_orders_table(self, open_orders_by_team: dict):
        try:
            # Connect to your Render Postgres Database
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()

            # Create table on first write
            if self.first_write_orders:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS open_orders (
                        team VARCHAR(100) PRIMARY KEY,
                        buy_order_id VARCHAR(100),
                        sell_order_id VARCHAR(100)
                    );
                """)

                self.first_write_orders = False

            for team in TEAM_LIST:
                buy_order_id, sell_order_id = open_orders_by_team.get(team, (None, None))
                cur.execute(
                    """
                    INSERT INTO open_orders (team, buy_order_id, sell_order_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (team) DO UPDATE 
                    SET buy_order_id = EXCLUDED.buy_order_id, sell_order_id = EXCLUDED.sell_order_id;
                    """,
                    (team, buy_order_id, sell_order_id)
                )

            # Commit changes to make them permanent
            conn.commit()
            cur.close()
            conn.close()
            print(f"Successfully saved open_order data table", flush=True)

        except Exception as e:
            print(f"Database operation failed: {e}", flush=True)
            
        finally:
            # Always close connections to avoid leaking resources
            if 'cur' in locals(): cur.close()
            if 'conn' in locals(): conn.close()

    @staticmethod
    def calculateMargin(tgt_price, current_price, unit_size, buy=True):
        margin = (tgt_price - current_price) / current_price if current_price != 0 else 0
        if buy:
            if margin < 0.2:
                adj_unit_size = UNIT_SIZE * 3
            elif margin < 0.1:
                adj_unit_size = UNIT_SIZE * 2
            else:
                adj_unit_size = UNIT_SIZE
        else:
            if margin < -0.2:
                adj_unit_size = UNIT_SIZE * 3
            elif margin < -0.1:
                adj_unit_size = UNIT_SIZE * 2
            else:
                adj_unit_size = UNIT_SIZE

        return adj_unit_size

    def debugPrint(self, message):
        if self.debug:
            print(message, flush=True)




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
    trader.debugPrint("Portfolio: ")
    trader.debugPrint(portfolio)

    # Buy/sell counts
    team_buy_sell_count = {}
    
    # Get market info
    unsuccessful_attempts = 0
    max_unsuccessful = 15
    count = 0
    amount_max = 10000
    net_session_purchases = 0

    # Open Orders
    open_orders_dict = trader.get_open_orders_as_json()
    if open_orders_dict == None:
        open_orders_dict = {t: (None, None) for t in TEAM_LIST}

    # Net Session Purchases by Team
    net_session_team_purchases = trader.get_net_trade_table_as_json()
    if net_session_team_purchases == None:
        net_session_team_purchases = {t: (0, 0) for t in TEAM_LIST}

    # Main Loop
    while count < amount_max:
        if net_session_purchases >= TOTAL_NET_PURCHASE_MAX:
            print(f'Reached max session purchases of {TOTAL_NET_PURCHASE_MAX}', flush=True)
            break

        for team in TEAM_LIST:

            kalshi_ticker = kalshi_tickers.get(team)
            market = trader.get_market(kalshi_ticker)

            expected_cents_value = trader.get_current_team_value(team)
            expected_cents = float(expected_cents_value) if expected_cents_value else 0

            # # TESTING ONLY
            # trader.buy_shares(kalshi_ticker, team, quantity=5, price_limit=0.1)
            # sys.exit()

            # Expected range
            sell_target = expected_cents * (1 + SELL_BUFFER)
            buy_target = expected_cents * (1 - BUY_BUFFER)

            ask_price = round(float(market['market']['yes_ask_dollars']) * 100, 2)
            ask_amount = market['market']['yes_ask_size_fp']
            bid_price = round(float(market['market']['yes_bid_dollars']) * 100, 2)
            bid_amount = market['market']['yes_bid_size_fp']

            # Status every x runs
            every_x_runs = 10
            if count % every_x_runs == 0:
                trader.debugPrint(f"Team: {team}")
                trader.debugPrint(f"Buy target: {round(buy_target, 2)} cents")
                trader.debugPrint(f"Sell target: {round(sell_target, 2)} cents")
                trader.debugPrint(f"Market ask (price, amount): {round(ask_price, 2)} cents, {ask_amount} shares")
                trader.debugPrint(f"Market bid (price, amount): {round(bid_price, 2)} cents, {bid_amount} shares")
    


            # Get buy-sell counts
            buy_count, sell_count = trader.get_buy_sell_count(team)
            # print(f'{team} buy count: {buy_count}', flush=True)
            # print(f'{team} sell count: {sell_count}', flush=True)

            no_sell = True if team in BUY_ONLY_TEAMS else False
            no_buy = True if team in SELL_ONLY_TEAMS else False

            
            # BUY SIDE:

            if not no_buy:
                if buy_target > ask_price:

                    # TAKER:

                    adj_unit_size = trader.calculateMargin(tgt_price=buy_target, current_price=ask_price, unit_size=UNIT_SIZE, buy=True)
                    try:
                        buy_amount, buy_quantity, shares_still_avail, order_id = trader.buy_shares(kalshi_ticker, team, quantity=adj_unit_size, price_limit=ask_price)
                        all_time_team_net_purchase, all_time_net_shares_purchase = net_session_team_purchases[team]
                        all_time_team_net_purchase += buy_amount
                        all_time_net_shares_purchase += buy_quantity
                        net_session_team_purchases[team] = (all_time_team_net_purchase, all_time_net_shares_purchase)
                        all_time_purchase = round((buy_count + buy_amount) / 100, 2)
                        net_session_purchases += all_time_purchase
                        # print(f"Buy order: {buy_result}")
                        print(f'\n!!! Bought {buy_quantity} shares for {team} at {ask_price} cents', flush=True)
                        print(f'!!! {team} session buy amount: ${all_time_purchase}!!!...\n', flush=True)
                                
                        # print(f"Ask price {ask_price} cents is below buy target {buy_target} cents")
                        buy_count += buy_amount
                        trader.update_buy_sell_count(team, buy_count=buy_count)
                        
                    except Exception as e:
                        print(f'Unsuccessful buy attempt: {e}', flush=True)
                        unsuccessful_attempts += 1
                        if unsuccessful_attempts > max_unsuccessful:
                            sys.exit()

                else:

                    trader.debugPrint('Hit buy else block...')
                    time.sleep(2)

                    # MAKER
                    open_order_ids = open_orders_dict.get(team, (None, None))
                    open_buy_order_id, open_sell_order_id = open_order_ids
                    if open_buy_order_id:

                        print(f'Open buy order {open_buy_order_id} exists for {team}...', flush=True)
                        time.sleep(2)
                        
                        net_transaction_amount, net_fees_amount, orders_filled = trader.get_and_update_order_status(open_buy_order_id, team=team)
                        if net_transaction_amount is not None:
                            all_time_team_net_purchase, all_time_net_shares_purchase = net_session_team_purchases[team]
                            all_time_team_net_purchase += net_transaction_amount
                            all_time_net_shares_purchase += orders_filled
                            net_session_team_purchases[team] = (all_time_team_net_purchase, all_time_net_shares_purchase)
                            open_orders_dict[team] = (None, open_sell_order_id)

                            # Add fee logic here...


                    else:
                        print(f'Open order does not exist for {team}... Creating now...', flush=True)
                        time.sleep(2)

                        buy_amount, buy_quantity, shares_still_avail, order_id = trader.buy_shares(kalshi_ticker, team, quantity=MAKER_UNIT_SIZE, price_limit=buy_target, maker=True)
                        open_orders_dict[team] = (order_id, open_sell_order_id)

            

            # SELL SIDE

            if not no_sell:
                if sell_target < bid_price:


                    # TAKER

                    adj_unit_size = trader.calculateMargin(tgt_price=buy_target, current_price=ask_price, unit_size=UNIT_SIZE, buy=False)

                    # Ensures we don't sell more than we buy
                    if buy_count > sell_count:
                        try:
                            sell_amount, sell_quantity, shares_still_avail, order_id = trader.sell_shares(kalshi_ticker, team, quantity=adj_unit_size, price_limit=bid_price, maker=False)
                            # print(f"Sell order: {sell_result}")
                            all_time_team_net_purchase, all_time_net_shares_purchase = net_session_team_purchases[team]
                            all_time_team_net_purchase -= sell_amount
                            all_time_net_shares_purchase -= sell_quantity
                            net_session_team_purchases[team] = (all_time_team_net_purchase, all_time_net_shares_purchase)
                            all_time_sell = round((sell_count + sell_amount) / 100, 2)
                            net_session_purchases -= all_time_sell
                            print(f'\n...!!! Sold {sell_quantity} shares for {team} at {bid_price} cents', flush=True)
                            print(f'!!! {team} session sell amount: ${all_time_sell}!!!...\n', flush=True)
                            # print(f"Bid price {bid_price} cents is above sell target {sell_target} cents")
                            
                            sell_count += sell_amount
                            trader.update_buy_sell_count(team, sell_count=sell_count)
                            
                                
                        except Exception as e:
                            print(f'Unsuccessful buy attempt: {e}', flush=True)
                            unsuccessful_attempts += 1
                            if unsuccessful_attempts > max_unsuccessful:
                                sys.exit()

                else:

                    # MAKER
                    open_order_ids = open_orders_dict.get(team, (None, None))
                    open_buy_order_id, open_sell_order_id = open_order_ids
                    if open_sell_order_id:

                        print(f'Open sell order {open_sell_order_id} exists for {team}...', flush=True)
                        time.sleep(2)

                        net_transaction_amount, net_fees_amount, orders_filled = trader.get_and_update_order_status(open_sell_order_id, team=team)
                        if net_transaction_amount is not None:
                            all_time_team_net_purchase, all_time_net_shares_purchase = net_session_team_purchases[team]
                            all_time_team_net_purchase -= net_transaction_amount
                            all_time_net_shares_purchase -= orders_filled
                            net_session_team_purchases[team] = (all_time_team_net_purchase, all_time_net_shares_purchase)
                            open_orders_dict[team] = (open_buy_order_id, None)

                            # Add fee logic here...
                    

                    else:
                        print(f'Open order does not exist for {team}... Creating now...', flush=True)
                        time.sleep(2)

                        sell_amount, sell_quantity, shares_still_avail, order_id = trader.sell_shares(kalshi_ticker, team, quantity=MAKER_UNIT_SIZE, price_limit=sell_target, maker=True)
                        open_orders_dict[team] = (open_buy_order_id, order_id)


        # UPDATE DB TABLES
        trader.write_to_open_orders_table(open_orders_dict)
        trader.write_to_net_trade_table(net_session_team_purchases)

        # SLEEP & LOOP
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
