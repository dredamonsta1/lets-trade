"""Tests for order book module."""

from datetime import datetime

import pytest

from python.ib_connector import TickData
from python.orderbook import (
    OrderBook,
    OrderBookManager,
    calculate_fair_value,
    calculate_quote_prices,
)


def make_tick(
    symbol: str = "AAPL",
    bid: float = 150.0,
    ask: float = 150.10,
    bid_size: int = 100,
    ask_size: int = 100,
) -> TickData:
    """Helper to create tick data."""
    return TickData(
        timestamp=datetime.now(),
        symbol=symbol,
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
        last=(bid + ask) / 2,
        volume=1000,
    )


class TestOrderBook:
    """Tests for OrderBook class."""

    def test_init(self):
        """Test order book initialization."""
        book = OrderBook("AAPL")

        assert book.symbol == "AAPL"
        assert book.bid == 0.0
        assert book.ask == 0.0
        assert book.mid == 0.0
        assert book.spread == 0.0

    def test_update_from_tick(self):
        """Test updating order book from tick data."""
        book = OrderBook("AAPL")
        tick = make_tick(bid=150.0, ask=150.10)

        book.update(tick)

        assert book.bid == 150.0
        assert book.ask == 150.10
        assert book._bid_size == 100
        assert book._ask_size == 100

    def test_mid_price(self):
        """Test mid price calculation."""
        book = OrderBook("AAPL")
        book.update(make_tick(bid=150.0, ask=150.10))

        assert book.mid == 150.05

    def test_mid_price_falls_back_to_last(self):
        """Test mid price uses last when bid/ask not available."""
        book = OrderBook("AAPL")
        book._last = 150.05

        assert book.mid == 150.05

    def test_spread(self):
        """Test spread calculation."""
        book = OrderBook("AAPL")
        book.update(make_tick(bid=150.0, ask=150.10))

        assert book.spread == pytest.approx(0.10)

    def test_spread_bps(self):
        """Test spread in basis points."""
        book = OrderBook("AAPL")
        book.update(make_tick(bid=150.0, ask=150.10))

        # spread_bps = (0.10 / 150.05) * 10000 ≈ 6.67
        assert book.spread_bps == pytest.approx(6.67, rel=0.01)

    def test_imbalance_balanced(self):
        """Test imbalance when book is balanced."""
        book = OrderBook("AAPL")
        book.update(make_tick(bid_size=100, ask_size=100))

        assert book.imbalance == 0.0

    def test_imbalance_buy_pressure(self):
        """Test imbalance with buying pressure."""
        book = OrderBook("AAPL")
        book.update(make_tick(bid_size=150, ask_size=50))

        # (150 - 50) / 200 = 0.5
        assert book.imbalance == pytest.approx(0.5)

    def test_imbalance_sell_pressure(self):
        """Test imbalance with selling pressure."""
        book = OrderBook("AAPL")
        book.update(make_tick(bid_size=50, ask_size=150))

        # (50 - 150) / 200 = -0.5
        assert book.imbalance == pytest.approx(-0.5)

    def test_snapshot(self):
        """Test getting order book snapshot."""
        book = OrderBook("AAPL")
        book.update(make_tick(bid=150.0, ask=150.10, bid_size=100, ask_size=100))

        snapshot = book.snapshot()

        assert snapshot.symbol == "AAPL"
        assert snapshot.bid == 150.0
        assert snapshot.ask == 150.10
        assert snapshot.mid == 150.05
        assert snapshot.spread == pytest.approx(0.10)
        assert snapshot.imbalance == 0.0

    def test_callback_on_update(self):
        """Test that callbacks are called on updates."""
        book = OrderBook("AAPL")
        callback_called = []

        def callback(snapshot):
            callback_called.append(snapshot)

        book.add_callback(callback)
        book.update(make_tick(bid=150.0, ask=150.10))

        assert len(callback_called) == 1
        assert callback_called[0].bid == 150.0

    def test_remove_callback(self):
        """Test removing a callback."""
        book = OrderBook("AAPL")
        callback_called = []

        def callback(snapshot):
            callback_called.append(snapshot)

        book.add_callback(callback)
        book.remove_callback(callback)
        book.update(make_tick())

        assert len(callback_called) == 0


class TestOrderBookManager:
    """Tests for OrderBookManager class."""

    def test_get_book_creates_new(self):
        """Test that get_book creates new order books."""
        manager = OrderBookManager()

        book = manager.get_book("AAPL")

        assert book.symbol == "AAPL"
        assert "AAPL" in manager._books

    def test_get_book_returns_existing(self):
        """Test that get_book returns existing books."""
        manager = OrderBookManager()
        book1 = manager.get_book("AAPL")
        book2 = manager.get_book("AAPL")

        assert book1 is book2

    def test_update_routes_to_correct_book(self):
        """Test that updates go to the correct order book."""
        manager = OrderBookManager()

        manager.update(make_tick(symbol="AAPL", bid=150.0))
        manager.update(make_tick(symbol="MSFT", bid=400.0))

        assert manager.get_book("AAPL").bid == 150.0
        assert manager.get_book("MSFT").bid == 400.0

    def test_snapshot_all(self):
        """Test getting snapshots of all books."""
        manager = OrderBookManager()
        manager.update(make_tick(symbol="AAPL", bid=150.0))
        manager.update(make_tick(symbol="MSFT", bid=400.0))

        snapshots = manager.snapshot_all()

        assert "AAPL" in snapshots
        assert "MSFT" in snapshots
        assert snapshots["AAPL"].bid == 150.0
        assert snapshots["MSFT"].bid == 400.0


class TestFairValueCalculation:
    """Tests for fair value calculation."""

    def test_fair_value_no_inventory(self):
        """Test fair value with no position."""
        book = OrderBook("AAPL")
        book.update(make_tick(bid=150.0, ask=150.10))

        fair_value = calculate_fair_value(book, inventory=0)

        assert fair_value == pytest.approx(150.05)

    def test_fair_value_long_position(self):
        """Test fair value is lower when long."""
        book = OrderBook("AAPL")
        book.update(make_tick(bid=150.0, ask=150.10))

        # Long 100 shares should lower fair value
        fair_value = calculate_fair_value(book, inventory=100, inventory_skew=0.0001)

        # Expected: 150.05 - 100 * 0.0001 * 150.05 = 150.05 - 1.5005 = 148.5495
        assert fair_value < 150.05

    def test_fair_value_short_position(self):
        """Test fair value is higher when short."""
        book = OrderBook("AAPL")
        book.update(make_tick(bid=150.0, ask=150.10))

        # Short 100 shares should raise fair value
        fair_value = calculate_fair_value(book, inventory=-100, inventory_skew=0.0001)

        assert fair_value > 150.05


class TestQuotePriceCalculation:
    """Tests for quote price calculation."""

    def test_quote_prices_symmetric(self):
        """Test quote prices are symmetric around fair value."""
        bid, ask = calculate_quote_prices(fair_value=100.0, spread_bps=10.0)

        mid = (bid + ask) / 2
        assert mid == pytest.approx(100.0, rel=0.001)

    def test_quote_prices_spread(self):
        """Test that spread is correct."""
        bid, ask = calculate_quote_prices(fair_value=100.0, spread_bps=10.0)

        spread = ask - bid
        spread_bps = (spread / 100.0) * 10000
        assert spread_bps == pytest.approx(10.0, rel=0.01)

    def test_quote_prices_min_spread(self):
        """Test minimum spread enforcement."""
        bid, ask = calculate_quote_prices(fair_value=1.0, spread_bps=1.0, min_spread=0.02)

        spread = ask - bid
        assert spread >= 0.02

    def test_quote_prices_zero_fair_value(self):
        """Test handling of zero fair value."""
        bid, ask = calculate_quote_prices(fair_value=0.0, spread_bps=10.0)

        assert bid == 0.0
        assert ask == 0.0
