import os
import re # Added
from datetime import datetime, timedelta
from typing import List, Dict, Any

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetAssetsRequest, GetOrdersRequest
from alpaca.trading.enums import AssetClass, OrderSide, OrderType, TimeInForce
from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import OptionSnapshotRequest, StockLatestBarRequest
from alpaca.data.enums import DataFeed

# Import settings for configurable risk-free rate and default IV
from .config import settings

def _parse_alpaca_option_symbol(alpaca_option_symbol: str) -> Dict[str, Any]:
    """
    Parses an Alpaca option symbol (e.g., 'SPY240719C00450000') into its components.
    Returns a dict with 'underlying_symbol', 'expiration_date', 'option_type', 'strike'.
    """
    # Regex to match common Alpaca option symbol format
    # Example: AAPL240719C00150000
    # Group 1: Underlying (e.g., AAPL)
    # Group 2: Expiration Date YYMMDD (e.g., 240719)
    # Group 3: Option Type C/P (e.g., C)
    # Group 4: Strike Price (e.g., 00150000 -> 150.00)
    match = re.match(r"([A-Z]+)(\d{6})([CP])(\d{8})", alpaca_option_symbol)
    if not match:
        raise ValueError(f"Could not parse Alpaca option symbol: {alpaca_option_symbol}")

    underlying_symbol, expiration_str, option_type_char, strike_str = match.groups()

    expiration_date = datetime.strptime(expiration_str, '%y%m%d').date()
    option_type = 'call' if option_type_char == 'C' else 'put'
    
    # Strike price needs to be converted from string like '00150000' to float '150.00'
    # Assuming the last two digits are always decimals for strike
    strike = float(strike_str[:-2] + '.' + strike_str[-2:])

    return {
        'underlying_symbol': underlying_symbol,
        'expiration_date': expiration_date,
        'option_type': option_type,
        'strike': strike
    }


class AlpacaConnector:
    def __init__(self):
        self.api_key = os.getenv("ALPACA_API_KEY")
        self.secret_key = os.getenv("ALPACA_SECRET_KEY")
        # Alpaca-py uses a 'paper' boolean flag instead of base_url for paper trading
        self.paper_trading = os.getenv("ALPACA_PAPER_TRADING", "True").lower() == "true"

        if not self.api_key or not self.secret_key:
            raise ValueError("ALPACA_API_KEY and ALPACA_SECRET_KEY environment variables must be set.")

        self.trading_client = TradingClient(self.api_key, self.secret_key, paper=self.paper_trading)
        self.option_data_client = OptionHistoricalDataClient(self.api_key, self.secret_key)
        self.stock_data_client = StockHistoricalDataClient(self.api_key, self.secret_key)

        print(f"AlpacaConnector initialized for {'paper' if self.paper_trading else 'live'} trading.")
        # Confirm connection by fetching and printing account info
        self.get_account_info()

    def get_current_price(self, symbol: str) -> float:
        """Fetches the current last price for a given stock symbol."""
        try:
            request_params = StockLatestBarRequest(symbol_or_symbols=[symbol])
            latest_bar = self.stock_data_client.get_stock_latest_bar(request_params)
            
            if latest_bar and symbol in latest_bar:
                price = latest_bar[symbol].close
                print(f"Fetched current price for {symbol}: {price}")
                return price
            else:
                print(f"Could not get latest bar for {symbol}")
                return None
        except Exception as e:
            print(f"Error fetching current price for {symbol}: {e}")
            return None

    def get_options_positions(self) -> List[Dict[str, Any]]:
        """
        Fetches current options positions from the Alpaca account and formats them
        for the options hedging strategy.
        """
        try:
            positions = self.trading_client.get_all_positions()
            options_positions_raw = [p for p in positions if p.asset_class == AssetClass.US_OPTION]
            print(f"Alpaca API returned {len(options_positions_raw)} raw options positions.")

            formatted_positions = []
            for pos in options_positions_raw:
                try:
                    parsed_symbol = _parse_alpaca_option_symbol(pos.symbol)
                except ValueError as e:
                    print(f"Skipping unparseable option symbol {pos.symbol}: {e}")
                    continue

                # Calculate time to expiration in years
                today = datetime.now().date()
                time_to_expiration_days = (parsed_symbol['expiration_date'] - today).days
                time_to_expiration_years = max(time_to_expiration_days / 365.0, 0.001) # Avoid T=0

                formatted_positions.append({
                    'symbol': pos.symbol, # Alpaca's full option symbol
                    'underlying_symbol': parsed_symbol['underlying_symbol'],
                    'strike': parsed_symbol['strike'],
                    'time': time_to_expiration_years,
                    'type': parsed_symbol['option_type'],
                    'quantity': int(pos.qty),
                    'expiration_date': parsed_symbol['expiration_date'].isoformat(),
                    'iv': 0.0 # Placeholder, will be updated
                })
            
            # Now fetch IV for each position
            for p in formatted_positions:
                iv = self.get_implied_volatility(
                    p['symbol'], # Use Alpaca's full option symbol
                    p['strike'],
                    p['expiration_date'],
                    p['type']
                )
                p['iv'] = iv if iv is not None else settings.options_default_implied_volatility # Use configurable default IV

            print(f"Fetched and formatted {len(formatted_positions)} options positions with IV.")
            return formatted_positions
        except Exception as e:
            print(f"Error fetching options positions: {e}")
            return []

    def get_implied_volatility(self, alpaca_option_symbol: str, strike: float, expiration_date: str, option_type: str) -> float:
        """
        Fetches implied volatility for a specific Alpaca option contract symbol.
        """
        try:
            request_params = OptionSnapshotRequest(symbol_or_symbols=[alpaca_option_symbol])
            snapshots = self.option_data_client.get_option_snapshot(request_params)
            
            if snapshots and alpaca_option_symbol in snapshots:
                snapshot = snapshots[alpaca_option_symbol]
                if snapshot.implied_volatility is not None:
                    return snapshot.implied_volatility
                elif snapshot.greeks and snapshot.greeks.vega is not None:
                    # If IV is not direct, sometimes vega is available, but we need IV for Black-Scholes
                    # For now, if IV is missing, return a default or calculate from price (more complex)
                    print(f"Implied Volatility not directly available for {alpaca_option_symbol} in snapshot, returning default.")
                    return settings.options_default_implied_volatility # Use configurable default IV
            
            print(f"Could not get option snapshot for {alpaca_option_symbol}. Returning default IV.")
            return settings.options_default_implied_volatility # Use configurable default IV
        except Exception as e:
            print(f"Error fetching implied volatility for {alpaca_option_symbol}: {e}")
            return settings.options_default_implied_volatility # Use configurable default IV

    def get_risk_free_rate(self) -> float:
        """
        Provides a risk-free interest rate, now read from settings.
        """
        return settings.options_risk_free_rate

    def place_order(self, symbol: str, qty: int, side: str, order_type: str = 'market', time_in_force: str = 'day'):
        """
        Places a trade order.
        :param symbol: The stock symbol to trade.
        :param qty: The number of shares.
        :param side: 'buy' or 'sell'.
        :param order_type: 'market', 'limit', 'stop', etc.
        :param time_in_force: 'day', 'gtc', 'opg', etc.
        """
        try:
            # Map string inputs to Alpaca-py enums
            alpaca_side = OrderSide.BUY if side.lower() == 'buy' else OrderSide.SELL
            alpaca_order_type = OrderType.MARKET if order_type.lower() == 'market' else OrderType.LIMIT # Add more types as needed
            alpaca_time_in_force = TimeInForce.DAY if time_in_force.lower() == 'day' else TimeInForce.GTC # Add more types as needed

            order = self.trading_client.submit_order(
                symbol=symbol,
                qty=qty,
                side=alpaca_side,
                type=alpaca_order_type,
                time_in_force=alpaca_time_in_force
            )
            print(f"Order placed: {order.id} - {side} {qty} {symbol} at {order_type}")
            return order
        except Exception as e:
            print(f"Error placing order for {symbol}: {e}")
            return None

    def get_account_info(self):
        """Fetches and returns account information."""
        try:
            account = self.trading_client.get_account()
            print(f"Account Info: Status={account.status}, Equity={account.equity}")
            return account
        except Exception as e:
            print(f"Error fetching account info: {e}")
            return None