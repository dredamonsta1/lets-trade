"""Market making strategy implementation."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog

from python.config import RiskSettings, settings
from python.orderbook import OrderBookSnapshot, calculate_fair_value, calculate_quote_prices
from python.strategy.base import Order, Strategy, StrategyState

logger = structlog.get_logger(__name__)


@dataclass
class MarketMakerConfig:
    """Configuration for market making strategy."""

    # Quote parameters
    spread_bps: float = 10.0  # Desired spread in basis points
    min_spread_bps: float = 5.0  # Minimum acceptable spread
    quote_size: int = 10  # Size of each quote
    max_position: int = 100  # Maximum position size

    # Inventory management
    inventory_skew: float = 0.0001  # Price adjustment per unit of inventory

    # Risk parameters
    max_daily_loss: float = 1000.0  # Kill switch trigger
    quote_refresh_ms: int = 100  # Minimum time between quote updates

    # Filters
    min_book_spread_bps: float = 1.0  # Don't quote if spread too tight
    max_book_spread_bps: float = 100.0  # Don't quote if spread too wide

    @classmethod
    def from_risk_settings(cls, risk: RiskSettings) -> "MarketMakerConfig":
        """Create config from risk settings."""
        return cls(
            min_spread_bps=risk.min_spread_bps,
            quote_size=risk.quote_size,
            max_position=risk.max_position_size,
            max_daily_loss=risk.max_daily_loss,
        )


class MarketMakerStrategy(Strategy):
    """
    Market making strategy that provides liquidity by quoting bid/ask.

    Core Logic:
    1. Calculate fair value (mid-price adjusted for inventory)
    2. Set bid/ask quotes around fair value
    3. Manage inventory risk by skewing quotes
    4. Refresh quotes as market moves
    """

    def __init__(self, symbol: str, config: MarketMakerConfig | None = None) -> None:
        super().__init__(symbol)
        self.config = config or MarketMakerConfig.from_risk_settings(settings.risk)

        # Strategy state
        self._last_snapshot: OrderBookSnapshot | None = None
        self._last_quote_time: datetime = datetime.min
        self._current_bid_order: Order | None = None
        self._current_ask_order: Order | None = None

        # Risk tracking
        self._daily_pnl: float = 0.0
        self._day_start: datetime = datetime.now().replace(hour=0, minute=0, second=0)
        self._kill_switch_triggered: bool = False

    def _should_quote(self, snapshot: OrderBookSnapshot) -> bool:
        """Determine if we should be quoting in current market conditions."""
        if self.state != StrategyState.RUNNING:
            return False

        if self._kill_switch_triggered:
            logger.warning("Kill switch active, not quoting")
            return False

        # Check spread conditions
        if snapshot.spread_bps < self.config.min_book_spread_bps:
            logger.debug("Market spread too tight", spread_bps=snapshot.spread_bps)
            return False

        if snapshot.spread_bps > self.config.max_book_spread_bps:
            logger.debug("Market spread too wide", spread_bps=snapshot.spread_bps)
            return False

        # Check position limits
        if abs(self.position.quantity) >= self.config.max_position:
            logger.warning("Position limit reached", position=self.position.quantity)
            return False

        return True

    def _calculate_quote_sizes(self) -> tuple[int, int]:
        """
        Calculate bid/ask sizes based on inventory.

        When long: reduce bid size, increase ask size
        When short: increase bid size, reduce ask size
        """
        base_size = self.config.quote_size

        # Calculate skew based on position
        position_pct = self.position.quantity / self.config.max_position
        skew_factor = 1.0 - abs(position_pct) * 0.5  # Reduce size as position grows

        if self.position.quantity > 0:
            # Long: want to sell more
            bid_size = max(1, int(base_size * skew_factor))
            ask_size = base_size
        elif self.position.quantity < 0:
            # Short: want to buy more
            bid_size = base_size
            ask_size = max(1, int(base_size * skew_factor))
        else:
            bid_size = base_size
            ask_size = base_size

        return bid_size, ask_size

    def _needs_quote_refresh(self, snapshot: OrderBookSnapshot) -> bool:
        """Check if quotes need to be refreshed."""
        # Time-based refresh
        time_since_quote = datetime.now() - self._last_quote_time
        if time_since_quote < timedelta(milliseconds=self.config.quote_refresh_ms):
            return False

        # Price-based refresh
        if self._last_snapshot:
            mid_change = abs(snapshot.mid - self._last_snapshot.mid)
            if mid_change > snapshot.mid * 0.0001:  # 1bp move
                return True

        # Quote every refresh interval regardless
        return time_since_quote >= timedelta(milliseconds=self.config.quote_refresh_ms * 10)

    def on_book_update(self, snapshot: OrderBookSnapshot) -> None:
        """React to order book changes."""
        if not self._should_quote(snapshot):
            self._last_snapshot = snapshot
            return

        # Update unrealized PnL
        self.position.update_unrealized_pnl(snapshot.mid)

        # Check for quote refresh
        if self._needs_quote_refresh(snapshot):
            self._update_quotes(snapshot)

        self._last_snapshot = snapshot

    def _update_quotes(self, snapshot: OrderBookSnapshot) -> None:
        """Update bid/ask quotes based on current market."""
        # Calculate fair value with inventory adjustment
        fair_value = calculate_fair_value(
            book=self._create_mock_book(snapshot),
            inventory=self.position.quantity,
            inventory_skew=self.config.inventory_skew,
        )

        if fair_value <= 0:
            return

        # Calculate quote prices
        bid_price, ask_price = calculate_quote_prices(
            fair_value=fair_value,
            spread_bps=self.config.spread_bps,
            min_spread=0.01,
        )

        # Get quote sizes
        bid_size, ask_size = self._calculate_quote_sizes()

        # Create/update orders
        self._current_bid_order = Order(
            id=self._generate_order_id(),
            symbol=self.symbol,
            side="BUY",
            quantity=bid_size,
            price=bid_price,
            order_type="LMT",
        )
        self.active_orders[self._current_bid_order.id] = self._current_bid_order

        self._current_ask_order = Order(
            id=self._generate_order_id(),
            symbol=self.symbol,
            side="SELL",
            quantity=ask_size,
            price=ask_price,
            order_type="LMT",
        )
        self.active_orders[self._current_ask_order.id] = self._current_ask_order

        self._last_quote_time = datetime.now()

        logger.debug(
            "Quotes updated",
            symbol=self.symbol,
            bid=bid_price,
            bid_size=bid_size,
            ask=ask_price,
            ask_size=ask_size,
            fair_value=fair_value,
            position=self.position.quantity,
        )

    def _create_mock_book(self, snapshot: OrderBookSnapshot):
        """Create a mock order book object for fair value calculation."""
        from python.orderbook import OrderBook

        book = OrderBook(snapshot.symbol)
        book._bid = snapshot.bid
        book._ask = snapshot.ask
        book._bid_size = snapshot.bid_size
        book._ask_size = snapshot.ask_size
        return book

    def get_orders(self) -> list[Order]:
        """Get current orders to place."""
        orders = []
        if self._current_bid_order:
            orders.append(self._current_bid_order)
        if self._current_ask_order:
            orders.append(self._current_ask_order)
        return orders

    def on_fill(self, order: Order, fill_price: float, fill_qty: int) -> None:
        """Handle order fills with PnL tracking."""
        super().on_fill(order, fill_price, fill_qty)

        # Update daily PnL (simplified - actual impl needs trade tracking)
        if order.side == "SELL" and self.position.quantity < 0:
            # Closing or opening short
            pass
        elif order.side == "BUY" and self.position.quantity > 0:
            # Closing or opening long
            pass

        # Check kill switch
        if self._daily_pnl <= -self.config.max_daily_loss:
            self._trigger_kill_switch()

    def _trigger_kill_switch(self) -> None:
        """Emergency stop - cancel all orders and stop quoting."""
        logger.error(
            "KILL SWITCH TRIGGERED",
            daily_pnl=self._daily_pnl,
            max_loss=self.config.max_daily_loss,
            position=self.position.quantity,
        )
        self._kill_switch_triggered = True
        self.state = StrategyState.STOPPED
        self.active_orders.clear()
        self._current_bid_order = None
        self._current_ask_order = None

    def reset_daily(self) -> None:
        """Reset daily tracking (call at start of trading day)."""
        self._daily_pnl = 0.0
        self._day_start = datetime.now().replace(hour=0, minute=0, second=0)
        self._kill_switch_triggered = False
        logger.info("Daily reset complete", symbol=self.symbol)

    def get_status(self) -> dict:
        """Get detailed strategy status."""
        status = super().get_status()
        status.update(
            {
                "config": {
                    "spread_bps": self.config.spread_bps,
                    "quote_size": self.config.quote_size,
                    "max_position": self.config.max_position,
                },
                "daily_pnl": self._daily_pnl,
                "kill_switch": self._kill_switch_triggered,
                "current_bid": self._current_bid_order.price if self._current_bid_order else None,
                "current_ask": self._current_ask_order.price if self._current_ask_order else None,
            }
        )
        return status
