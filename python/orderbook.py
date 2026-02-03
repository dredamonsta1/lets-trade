"""In-memory order book reconstruction from market data."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import structlog

from python.ib_connector import TickData

logger = structlog.get_logger(__name__)


@dataclass
class PriceLevel:
    """A single price level in the order book."""

    price: float
    size: int
    order_count: int = 1
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class OrderBookSnapshot:
    """Point-in-time snapshot of order book state."""

    symbol: str
    timestamp: datetime
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    mid: float
    spread: float
    spread_bps: float
    imbalance: float


class OrderBook:
    """
    Maintains order book state from Level 1/Level 2 market data.

    For Level 1 (top of book only):
    - Tracks best bid/ask from tick updates

    For Level 2 (depth of market):
    - Maintains full price ladder (TODO: implement with IB DOM data)
    """

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self._bid: float = 0.0
        self._ask: float = 0.0
        self._bid_size: int = 0
        self._ask_size: int = 0
        self._last: float = 0.0
        self._volume: int = 0
        self._last_update: datetime = datetime.now()
        self._update_count: int = 0

        # Callbacks for order book updates
        self._callbacks: list[Callable[[OrderBookSnapshot], None]] = []

        # Level 2 depth (future use)
        self._bid_levels: dict[float, PriceLevel] = {}
        self._ask_levels: dict[float, PriceLevel] = {}

    @property
    def bid(self) -> float:
        """Best bid price."""
        return self._bid

    @property
    def ask(self) -> float:
        """Best ask price."""
        return self._ask

    @property
    def mid(self) -> float:
        """Mid price."""
        if self._bid > 0 and self._ask > 0:
            return (self._bid + self._ask) / 2.0
        return self._last

    @property
    def spread(self) -> float:
        """Absolute spread."""
        if self._bid > 0 and self._ask > 0:
            return self._ask - self._bid
        return 0.0

    @property
    def spread_bps(self) -> float:
        """Spread in basis points."""
        mid = self.mid
        if mid > 0:
            return (self.spread / mid) * 10000
        return 0.0

    @property
    def imbalance(self) -> float:
        """
        Order book imbalance: (bid_size - ask_size) / (bid_size + ask_size)

        Positive = more buying pressure
        Negative = more selling pressure
        """
        total = self._bid_size + self._ask_size
        if total == 0:
            return 0.0
        return (self._bid_size - self._ask_size) / total

    def update(self, tick: TickData) -> None:
        """Update order book from tick data."""
        changed = False

        if tick.bid > 0 and (tick.bid != self._bid or tick.bid_size != self._bid_size):
            self._bid = tick.bid
            self._bid_size = tick.bid_size
            changed = True

        if tick.ask > 0 and (tick.ask != self._ask or tick.ask_size != self._ask_size):
            self._ask = tick.ask
            self._ask_size = tick.ask_size
            changed = True

        if tick.last > 0:
            self._last = tick.last

        if tick.volume > 0:
            self._volume = tick.volume

        if changed:
            self._last_update = datetime.now()
            self._update_count += 1
            self._notify_callbacks()

    def snapshot(self) -> OrderBookSnapshot:
        """Get current order book state as a snapshot."""
        return OrderBookSnapshot(
            symbol=self.symbol,
            timestamp=self._last_update,
            bid=self._bid,
            ask=self._ask,
            bid_size=self._bid_size,
            ask_size=self._ask_size,
            mid=self.mid,
            spread=self.spread,
            spread_bps=self.spread_bps,
            imbalance=self.imbalance,
        )

    def add_callback(self, callback: Callable[[OrderBookSnapshot], None]) -> None:
        """Register callback for order book updates."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[OrderBookSnapshot], None]) -> None:
        """Remove a callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _notify_callbacks(self) -> None:
        """Notify all registered callbacks."""
        snapshot = self.snapshot()
        for callback in self._callbacks:
            try:
                callback(snapshot)
            except Exception as e:
                logger.error("Order book callback error", error=str(e))


class OrderBookManager:
    """Manages order books for multiple symbols."""

    def __init__(self) -> None:
        self._books: dict[str, OrderBook] = {}
        self._callbacks: list[Callable[[str, OrderBookSnapshot], None]] = []

    def get_book(self, symbol: str) -> OrderBook:
        """Get or create order book for a symbol."""
        if symbol not in self._books:
            self._books[symbol] = OrderBook(symbol)
            # Forward updates to manager-level callbacks
            self._books[symbol].add_callback(
                lambda snap, sym=symbol: self._on_book_update(sym, snap)
            )
        return self._books[symbol]

    def update(self, tick: TickData) -> None:
        """Update the appropriate order book from tick data."""
        book = self.get_book(tick.symbol)
        book.update(tick)

    def snapshot_all(self) -> dict[str, OrderBookSnapshot]:
        """Get snapshots of all order books."""
        return {symbol: book.snapshot() for symbol, book in self._books.items()}

    def add_callback(self, callback: Callable[[str, OrderBookSnapshot], None]) -> None:
        """Register callback for any order book update."""
        self._callbacks.append(callback)

    def _on_book_update(self, symbol: str, snapshot: OrderBookSnapshot) -> None:
        """Forward book updates to manager-level callbacks."""
        for callback in self._callbacks:
            try:
                callback(symbol, snapshot)
            except Exception as e:
                logger.error("Manager callback error", error=str(e), symbol=symbol)


def calculate_fair_value(
    book: OrderBook,
    inventory: int = 0,
    inventory_skew: float = 0.0001,
) -> float:
    """
    Calculate fair value with inventory adjustment.

    Args:
        book: Order book to calculate fair value from
        inventory: Current position (positive = long, negative = short)
        inventory_skew: Price adjustment per unit of inventory

    Returns:
        Fair value price adjusted for inventory
    """
    mid = book.mid
    if mid <= 0:
        return 0.0

    # Skew fair value based on inventory
    # Long position -> lower fair value (want to sell)
    # Short position -> higher fair value (want to buy)
    adjustment = -inventory * inventory_skew * mid

    return mid + adjustment


def calculate_quote_prices(
    fair_value: float,
    spread_bps: float = 10.0,
    min_spread: float = 0.01,
) -> tuple[float, float]:
    """
    Calculate bid/ask quote prices around fair value.

    Args:
        fair_value: The fair value to quote around
        spread_bps: Desired spread in basis points
        min_spread: Minimum absolute spread

    Returns:
        Tuple of (bid_price, ask_price)
    """
    if fair_value <= 0:
        return 0.0, 0.0

    half_spread = max(fair_value * spread_bps / 20000, min_spread / 2)

    bid = round(fair_value - half_spread, 2)
    ask = round(fair_value + half_spread, 2)

    return bid, ask
