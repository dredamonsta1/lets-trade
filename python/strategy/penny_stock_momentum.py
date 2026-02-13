"""
Penny Stock Momentum Strategy
Identifies penny stocks with high volume buys and sells them after a price jump
or when volume levels out/goes down.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Any

import structlog

from ...alpaca_connector import AlpacaConnector
from ...config import settings
from alpaca.data.enums import TimeFrame # Added

logger = structlog.get_logger(__name__)

class PennyStockMomentumStrategy:
    def __init__(self, alpaca_connector: AlpacaConnector):
        self.alpaca_connector = alpaca_connector
        self.penny_stock_price_threshold = settings.penny_stock_price_threshold
        self.penny_stock_market_cap_threshold = settings.penny_stock_market_cap_threshold
        self.volume_avg_days = settings.penny_stock_volume_avg_days
        self.volume_multiplier_entry = settings.penny_stock_volume_multiplier_entry
        self.price_increase_entry_percent = settings.penny_stock_price_increase_entry_percent
        self.price_increase_entry_window_minutes = settings.penny_stock_price_increase_entry_window_minutes
        self.profit_target_min_percent = settings.penny_stock_profit_target_min_percent
        self.profit_target_max_percent = settings.penny_stock_profit_target_max_percent
        self.volume_decay_exit_percent = settings.penny_stock_volume_decay_exit_percent
        self.stop_loss_percent = settings.penny_stock_stop_loss_percent
        self.max_trade_amount = settings.penny_stock_max_trade_amount

        self.monitored_stocks: Dict[str, Any] = {} # Stores data for stocks being monitored
        self.open_positions: Dict[str, Any] = {} # Stores data for currently open positions

        logger.info("PennyStockMomentumStrategy initialized.")

    async def _get_and_filter_penny_stocks(self) -> List[str]:
        """
        Fetches all US stocks and filters them based on penny stock criteria.
        This can be a time-consuming operation and should not be run too frequently.
        """
        logger.info("Fetching and filtering penny stocks...")
        try:
            # Alpaca's get_all_assets() returns Asset objects
            all_assets = self.alpaca_connector.trading_client.get_all_assets(asset_class=AssetClass.US_EQUITY)
            
            penny_stocks = []
            for asset in all_assets:
                if asset.status == 'active' and asset.tradable:
                    # Need to get current price and market cap.
                    # Market cap is not directly available from asset object.
                    # This is a significant limitation. We might need an external data source
                    # or make assumptions. For now, we'll filter by price only.
                    
                    current_price = self.alpaca_connector.get_current_price(asset.symbol)
                    if current_price is not None and current_price < self.penny_stock_price_threshold:
                        # Placeholder for market cap check - requires external data or more complex Alpaca API usage
                        # For now, we'll assume all stocks under $5 are potential penny stocks
                        penny_stocks.append(asset.symbol)
            
            logger.info(f"Found {len(penny_stocks)} potential penny stocks based on price threshold.")
            return penny_stocks
        except Exception as e:
            logger.error("Error fetching and filtering penny stocks", error=e)
            return []

    async def _get_historical_volume(self, symbol: str) -> float:
        """
        Fetches 50-day average volume for a given symbol.
        """
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=self.volume_avg_days * 1.5) # Fetch a bit more to ensure 50 trading days
            
            bars = self.alpaca_connector.stock_data_client.get_stock_bars(
                symbol_or_symbols=[symbol],
                timeframe=TimeFrame.Day, # Assuming daily bars for 50-day average
                start=start_date,
                end=end_date
            )
            
            if bars and symbol in bars:
                daily_volumes = [bar.volume for bar in bars[symbol]]
                if len(daily_volumes) >= self.volume_avg_days:
                    avg_volume = sum(daily_volumes[-self.volume_avg_days:]) / self.volume_avg_days
                    return avg_volume
            logger.warning(f"Could not get enough historical volume for {symbol} to calculate {self.volume_avg_days}-day average.")
            return 0.0
        except Exception as e:
            logger.error(f"Error fetching historical volume for {symbol}", error=e)
            return 0.0

    async def run_strategy(self):
        """
        Main loop for the penny stock momentum strategy.
        """
        # This will be a continuous loop, subscribing to data streams
        # and checking for signals.
        logger.info("PennyStockMomentumStrategy starting main loop.")

        # Initial scan for penny stocks
        penny_stock_symbols = await self._get_and_filter_penny_stocks()
        
        # Fetch initial historical data for average volume
        for symbol in penny_stock_symbols:
            avg_volume = await self._get_historical_volume(symbol)
            if avg_volume > 0:
                self.monitored_stocks[symbol] = {
                    'avg_volume_50_day': avg_volume,
                    'last_5min_bars': [], # To store recent 5-min bars for price change check
                    'current_price': 0.0,
                    'peak_volume_5min': 0.0 # For volume decay exit
                }
                logger.info(f"Monitoring {symbol} with 50-day avg volume: {avg_volume:.2f}")

        # Placeholder for streaming data logic
        # In a real implementation, this would connect to Alpaca's websocket
        # and process incoming 1-minute bars, aggregating them into 5-minute bars.
        while True:
            logger.info("Simulating real-time data processing for penny stock strategy...")
            # Simulate getting a 5-min bar for a monitored stock
            for symbol, data in self.monitored_stocks.items():
                # For demonstration, let's just check a dummy condition
                current_price = self.alpaca_connector.get_current_price(symbol)
                if current_price is None:
                    continue
                
                # Simulate 5-min bar data
                # In reality, this would come from aggregating 1-min streaming bars
                simulated_5min_bar = {
                    'open': current_price * 0.98, # Simulate a 2% jump
                    'close': current_price,
                    'high': current_price,
                    'low': current_price * 0.98,
                    'volume': data['avg_volume_50_day'] * self.volume_multiplier_entry * 1.1 # Simulate 3x volume jump
                }
                
                # Update last_5min_bars (keep a window of recent bars)
                data['last_5min_bars'].append(simulated_5min_bar)
                if len(data['last_5min_bars']) > self.price_increase_entry_window_minutes / 5 + 1: # Keep enough bars
                    data['last_5min_bars'].pop(0)

                # Check Entry Signal
                if self._check_entry_signal(symbol, data, current_price):
                    await self._execute_entry(symbol, current_price)
                
                # Check Exit Signals for open positions
                if symbol in self.open_positions:
                    await self._check_exit_signals(symbol, current_price, data)

            await asyncio.sleep(settings.penny_stock_strategy_interval_seconds) # Check every X seconds


    def _check_entry_signal(self, symbol: str, data: Dict[str, Any], current_price: float) -> bool:
        """
        Checks if the entry conditions are met for a given stock.
        """
        if symbol in self.open_positions: # Already in a position
            return False

        if not data['last_5min_bars'] or len(data['last_5min_bars']) < 2: # Need at least two 5-min bars
            return False

        # Check price increase (2% in last 5 minutes)
        # Assuming last_5min_bars contains bars in chronological order
        first_bar_in_window = data['last_5min_bars'][-(self.price_increase_entry_window_minutes // 5)]
        price_change_percent = (current_price - first_bar_in_window['open']) / first_bar_in_window['open'] * 100
        
        price_condition = price_change_percent >= self.price_increase_entry_percent

        # Check volume condition (3x 50-day average)
        current_5min_volume = data['last_5min_bars'][-1]['volume']
        volume_condition = current_5min_volume >= data['avg_volume_50_day'] * self.volume_multiplier_entry

        if price_condition and volume_condition:
            logger.info(f"ENTRY SIGNAL for {symbol}: Price up {price_change_percent:.2f}% in 5min, Volume {current_5min_volume:.2f} (vs avg {data['avg_volume_50_day']:.2f})")
            return True
        return False

    async def _execute_entry(self, symbol: str, entry_price: float):
        """
        Executes an entry trade for the given symbol.
        """
        trade_qty = int(self.max_trade_amount / entry_price)
        if trade_qty == 0:
            logger.warning(f"Trade quantity for {symbol} is 0 at price {entry_price}. Skipping entry.")
            return

        logger.info(f"Executing BUY order for {trade_qty} shares of {symbol} at {entry_price:.2f}")
        order = await self.alpaca_connector.place_order(
            symbol=symbol,
            qty=trade_qty,
            side='buy',
            order_type='market',
            time_in_force='day'
        )
        if order:
            self.open_positions[symbol] = {
                'entry_price': entry_price,
                'quantity': trade_qty,
                'entry_time': datetime.now(),
                'peak_volume_since_entry': 0.0 # To track for volume decay exit
            }
            logger.info(f"BUY order placed for {symbol}: {order.id}")
        else:
            logger.error(f"Failed to place BUY order for {symbol}.")

    async def _check_exit_signals(self, symbol: str, current_price: float, data: Dict[str, Any]):
        """
        Checks exit conditions for an open position.
        """
        position = self.open_positions[symbol]
        entry_price = position['entry_price']
        quantity = position['quantity']

        current_profit_percent = (current_price - entry_price) / entry_price * 100
        
        # Update peak volume since entry
        if data['last_5min_bars']:
            current_5min_volume = data['last_5min_bars'][-1]['volume']
            position['peak_volume_since_entry'] = max(position['peak_volume_since_entry'], current_5min_volume)

        # Exit Condition 1: Profit Target
        if self.profit_target_min_percent <= current_profit_percent <= self.profit_target_max_percent:
            logger.info(f"EXIT SIGNAL (Profit Target) for {symbol}: Price up {current_profit_percent:.2f}%. Selling {quantity} shares.")
            await self._execute_exit(symbol, quantity, current_price, 'profit_target')
            return
        
        # Exit Condition 2: Stop Loss
        if current_profit_percent <= -self.stop_loss_percent:
            logger.info(f"EXIT SIGNAL (Stop Loss) for {symbol}: Price down {current_profit_percent:.2f}%. Selling {quantity} shares.")
            await self._execute_exit(symbol, quantity, current_price, 'stop_loss')
            return

        # Exit Condition 3: Volume Decay
        if position['peak_volume_since_entry'] > 0 and data['last_5min_bars']:
            current_5min_volume = data['last_5min_bars'][-1]['volume']
            if current_5min_volume < position['peak_volume_since_entry'] * self.volume_decay_exit_percent:
                logger.info(f"EXIT SIGNAL (Volume Decay) for {symbol}: Volume dropped. Selling {quantity} shares.")
                await self._execute_exit(symbol, quantity, current_price, 'volume_decay')
                return

    async def _execute_exit(self, symbol: str, quantity: int, exit_price: float, reason: str):
        """
        Executes an exit trade for the given symbol.
        """
        logger.info(f"Executing SELL order for {quantity} shares of {symbol} at {exit_price:.2f} (Reason: {reason})")
        order = await self.alpaca_connector.place_order(
            symbol=symbol,
            qty=quantity,
            side='sell',
            order_type='market',
            time_in_force='day'
        )
        if order:
            del self.open_positions[symbol] # Remove from open positions
            logger.info(f"SELL order placed for {symbol}: {order.id}")
        else:
            logger.error(f"Failed to place SELL order for {symbol}.")
