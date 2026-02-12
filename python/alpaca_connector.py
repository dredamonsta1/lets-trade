import os
import alpaca_trade_api as tradeapi
from datetime import datetime, timedelta
import pandas as pd # Alpaca API often returns data in pandas DataFrames

class AlpacaConnector:
    def __init__(self):
        self.api_key = os.getenv("ALPACA_API_KEY")
        self.secret_key = os.getenv("ALPACA_SECRET_KEY")
        self.base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets") # Default to paper trading
        self.api = tradeapi.REST(self.api_key, self.secret_key, self.base_url, api_version='v2')

        if not self.api_key or not self.secret_key:
            raise ValueError("ALPACA_API_KEY and ALPACA_SECRET_KEY environment variables must be set.")

        print(f"AlpacaConnector initialized for base URL: {self.base_url}")

    def get_current_price(self, symbol: str) -> float:
        """Fetches the current last price for a given stock symbol."""
        try:
            bar = self.api.get_latest_bar(symbol)
            if bar:
                return bar.close
            else:
                print(f"Could not get latest bar for {symbol}")
                return None
        except Exception as e:
            print(f"Error fetching current price for {symbol}: {e}")
            return None

    def get_options_positions(self) -> list:
        """
        Fetches current options positions from the Alpaca account.
        Note: Alpaca's options API might require specific handling.
        This is a simplified placeholder.
        """
        print("Fetching options positions (Alpaca Options API integration is complex and requires specific contract details).")
        # Alpaca's options API is separate from the main API and requires specific endpoints.
        # This is a placeholder. A full implementation would involve:
        # 1. Getting account positions
        # 2. Filtering for options contracts
        # 3. Parsing relevant details (strike, expiration, type, quantity, etc.)
        # For now, returning an empty list or mock data.
        # Example structure needed by strategy: [{'strike': K, 'time': T, 'iv': sigma, 'type': 'call', 'quantity': Q}]
        
        # Mock data for testing the strategy without live Alpaca options positions
        # In a real scenario, this would come from Alpaca API
        mock_positions = [
            {'symbol': 'SPY', 'strike': 450, 'time': 0.1, 'iv': 0.20, 'type': 'call', 'quantity': 1},
            {'symbol': 'SPY', 'strike': 460, 'time': 0.1, 'iv': 0.18, 'type': 'put', 'quantity': -1},
        ]
        print("Returning mock options positions for demonstration.")
        return mock_positions


    def get_implied_volatility(self, symbol: str, strike: float, expiration_date: str, option_type: str) -> float:
        """
        Fetches implied volatility for a specific option contract.
        This is a complex operation with Alpaca's options API.
        Requires specific contract identification.
        """
        print(f"Attempting to fetch implied volatility for {symbol} {strike} {expiration_date} {option_type}")
        # Alpaca's options API for IV is not straightforward.
        # It typically involves getting quotes for a specific option contract.
        # For a robust solution, you'd need to:
        # 1. Find the exact option contract symbol using get_option_contracts.
        # 2. Get the latest quote for that contract.
        # 3. Calculate IV from the quote (bid/ask/mid price) using an options pricing model,
        #    or check if Alpaca provides it directly in a specific endpoint.
        
        # For now, returning a hardcoded IV for demonstration.
        return 0.25 # Placeholder IV

    def get_risk_free_rate(self) -> float:
        """
        Provides a risk-free interest rate. Alpaca does not directly provide this.
        This can be a hardcoded value, or fetched from an external source (e.g., FRED for T-bill yields).
        """
        # Placeholder: In a production system, fetch from a reliable external source
        # like FRED (Federal Reserve Economic Data) for US Treasury yields.
        return 0.04 # Example: 4% annual risk-free rate

    def place_order(self, symbol: str, qty: int, side: str, order_type: str = 'market', time_in_force: str = 'gtc'):
        """
        Places a trade order.
        :param symbol: The stock symbol to trade.
        :param qty: The number of shares.
        :param side: 'buy' or 'sell'.
        :param order_type: 'market', 'limit', 'stop', etc.
        :param time_in_force: 'day', 'gtc', 'opg', etc.
        """
        try:
            order = self.api.submit_order(
                symbol=symbol,
                qty=qty,
                side=side,
                type=order_type,
                time_in_force=time_in_force
            )
            print(f"Order placed: {order.id} - {side} {qty} {symbol} at {order_type}")
            return order
        except Exception as e:
            print(f"Error placing order for {symbol}: {e}")
            return None

    def get_account_info(self):
        """Fetches and returns account information."""
        try:
            account = self.api.get_account()
            print(f"Account Info: Status={account.status}, Equity={account.equity}")
            return account
        except Exception as e:
            print(f"Error fetching account info: {e}")
            return None
