from ..options_greeks import calculate_delta, calculate_gamma
import numpy as np
from typing import List, Dict, Any

# Assuming AlpacaConnector is passed in, so no direct import here to avoid circular dependency
# from ..alpaca_connector import AlpacaConnector

class OptionsHedgingStrategy:
    def __init__(self, risk_free_rate: float, alpaca_connector: Any): # Use Any for type hinting to avoid circular import
        self.risk_free_rate = risk_free_rate
        self.alpaca_connector = alpaca_connector
        self.delta_hedge_threshold = 50
        self.gamma_monitor_threshold = 10
        self.underlying_symbol = "SPY" # This should be configurable or derived from positions

    def update_risk_free_rate(self, new_rate: float):
        self.risk_free_rate = new_rate

    def rebalance_portfolio(self, current_price: float, positions: List[Dict[str, Any]]) -> float:
        """
        Calculates the total delta of current options positions and determines
        the number of shares needed to hedge.
        """
        total_delta = 0
        for pos in positions:
            delta = calculate_delta(
                current_price, pos['strike'], pos['time'],
                self.risk_free_rate, pos['iv'], pos['type']
            )
            total_delta += delta * pos['quantity']

        hedge_required = -total_delta
        print(f"Current Total Delta: {total_delta:.2f}, Hedge Required: {hedge_required:.2f} shares of {self.underlying_symbol}")
        return hedge_required

    def check_gamma_exposure(self, current_price: float, positions: List[Dict[str, Any]]) -> float:
        """
        Calculates the total gamma of current options positions for monitoring.
        """
        total_gamma = 0
        for p in positions:
            gamma = calculate_gamma(
                current_price, p['strike'], p['time'],
                self.risk_free_rate, p['iv']
            )
            total_gamma += gamma * p['quantity'] # Corrected 'qty' to 'quantity'

        print(f"Current Total Gamma Exposure: {total_gamma:.2f}")
        if abs(total_gamma) > self.gamma_monitor_threshold:
            print(f"WARNING: Total Gamma ({total_gamma:.2f}) exceeds monitoring threshold ({self.gamma_monitor_threshold})!")
        return total_gamma

    def execute_hedge(self, hedge_amount: float):
        """
        Executes the hedge trade using the AlpacaConnector.
        """
        if abs(hedge_amount) > 0: # Only place order if hedge is required
            side = 'buy' if hedge_amount > 0 else 'sell'
            qty = int(abs(hedge_amount)) # Alpaca requires integer quantity for shares

            if qty > 0: # Ensure quantity is positive
                print(f"Attempting to place hedge order: {side.upper()} {qty} shares of {self.underlying_symbol}")
                order = self.alpaca_connector.place_order(
                    symbol=self.underlying_symbol,
                    qty=qty,
                    side=side,
                    order_type='market', # Market order for quick execution
                    time_in_force='day'
                )
                if order:
                    print(f"Hedge order placed successfully: {order.id}")
                else:
                    print("Failed to place hedge order.")
            else:
                print("Calculated hedge quantity is zero, no order placed.")
        else:
            print("No hedge required at this time.")

    def run_strategy(self, current_price: float, options_positions: List[Dict[str, Any]]):
        """
        Runs the full hedging and monitoring logic.
        """
        print(f"\n--- Running Options Hedging Strategy at price {current_price} ---")

        # 1. Check Gamma Exposure
        self.check_gamma_exposure(current_price, options_positions)

        # 2. Determine Delta Hedge
        hedge_required = self.rebalance_portfolio(current_price, options_positions)

        # 3. Execute Hedge
        # In a real system, you'd compare hedge_required to current stock position
        # and only trade if there's a significant difference or if current position is known.
        # For simplicity, we'll execute if hedge_required is non-zero.
        self.execute_hedge(hedge_required)

        print("--- Strategy Run Complete ---")
