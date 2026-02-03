"""Tests for strategy module."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from python.orderbook import OrderBookSnapshot
from python.strategy.base import Order, Position, Strategy, StrategyMetrics, StrategyState
from python.strategy.market_maker import MarketMakerConfig, MarketMakerStrategy


class ConcreteStrategy(Strategy):
    """Concrete implementation of Strategy for testing."""

    def on_book_update(self, snapshot: OrderBookSnapshot) -> None:
        pass

    def get_orders(self) -> list[Order]:
        return []


def make_snapshot(
    symbol: str = "AAPL",
    bid: float = 150.0,
    ask: float = 150.10,
    bid_size: int = 100,
    ask_size: int = 100,
) -> OrderBookSnapshot:
    """Helper to create order book snapshot."""
    mid = (bid + ask) / 2
    spread = ask - bid
    spread_bps = (spread / mid) * 10000 if mid > 0 else 0
    imbalance = (bid_size - ask_size) / (bid_size + ask_size) if (bid_size + ask_size) > 0 else 0

    return OrderBookSnapshot(
        symbol=symbol,
        timestamp=datetime.now(),
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
        mid=mid,
        spread=spread,
        spread_bps=spread_bps,
        imbalance=imbalance,
    )


class TestPosition:
    """Tests for Position class."""

    def test_market_value(self):
        """Test market value calculation."""
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)

        assert pos.market_value == 15000.0

    def test_market_value_short(self):
        """Test market value for short position."""
        pos = Position(symbol="AAPL", quantity=-100, avg_cost=150.0)

        assert pos.market_value == 15000.0

    def test_unrealized_pnl_long(self):
        """Test unrealized PnL for long position."""
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)

        pos.update_unrealized_pnl(current_price=155.0)

        # (155 - 150) * 100 = 500
        assert pos.unrealized_pnl == 500.0

    def test_unrealized_pnl_short(self):
        """Test unrealized PnL for short position."""
        pos = Position(symbol="AAPL", quantity=-100, avg_cost=150.0)

        pos.update_unrealized_pnl(current_price=145.0)

        # (145 - 150) * -100 = 500
        assert pos.unrealized_pnl == 500.0


class TestStrategyMetrics:
    """Tests for StrategyMetrics class."""

    def test_win_rate_no_trades(self):
        """Test win rate with no trades."""
        metrics = StrategyMetrics()

        assert metrics.win_rate == 0.0

    def test_win_rate(self):
        """Test win rate calculation."""
        metrics = StrategyMetrics(
            total_trades=10,
            winning_trades=6,
            losing_trades=4,
        )

        assert metrics.win_rate == 60.0


class TestStrategy:
    """Tests for Strategy base class."""

    def test_init(self):
        """Test strategy initialization."""
        strategy = ConcreteStrategy("AAPL")

        assert strategy.symbol == "AAPL"
        assert strategy.state == StrategyState.INITIALIZING
        assert strategy.position.quantity == 0

    def test_start_stop(self):
        """Test starting and stopping strategy."""
        strategy = ConcreteStrategy("AAPL")

        strategy.start()
        assert strategy.state == StrategyState.RUNNING

        strategy.stop()
        assert strategy.state == StrategyState.STOPPED

    def test_pause_resume(self):
        """Test pausing and resuming strategy."""
        strategy = ConcreteStrategy("AAPL")
        strategy.start()

        strategy.pause()
        assert strategy.state == StrategyState.PAUSED

        strategy.resume()
        assert strategy.state == StrategyState.RUNNING

    def test_on_fill_updates_position(self):
        """Test that fills update position."""
        strategy = ConcreteStrategy("AAPL")
        order = Order(id="1", symbol="AAPL", side="BUY", quantity=100, price=150.0)

        strategy.on_fill(order, fill_price=150.0, fill_qty=100)

        assert strategy.position.quantity == 100
        assert strategy.position.avg_cost == 150.0
        assert strategy.metrics.total_trades == 1

    def test_on_fill_sell(self):
        """Test sell fill updates position."""
        strategy = ConcreteStrategy("AAPL")
        strategy.position.quantity = 100
        strategy.position.avg_cost = 150.0

        order = Order(id="1", symbol="AAPL", side="SELL", quantity=50, price=155.0)
        strategy.on_fill(order, fill_price=155.0, fill_qty=50)

        assert strategy.position.quantity == 50

    def test_generate_order_id(self):
        """Test order ID generation."""
        strategy = ConcreteStrategy("AAPL")

        id1 = strategy._generate_order_id()
        id2 = strategy._generate_order_id()

        assert id1 != id2
        assert "AAPL" in id1

    def test_get_status(self):
        """Test getting strategy status."""
        strategy = ConcreteStrategy("AAPL")
        strategy.start()
        strategy.position.quantity = 50
        strategy.position.avg_cost = 150.0

        status = strategy.get_status()

        assert status["symbol"] == "AAPL"
        assert status["state"] == "running"
        assert status["position"] == 50
        assert status["avg_cost"] == 150.0


class TestMarketMakerStrategy:
    """Tests for MarketMakerStrategy class."""

    def test_init(self):
        """Test market maker initialization."""
        config = MarketMakerConfig(
            spread_bps=15.0,
            quote_size=20,
            max_position=200,
        )
        strategy = MarketMakerStrategy("AAPL", config=config)

        assert strategy.symbol == "AAPL"
        assert strategy.config.spread_bps == 15.0
        assert strategy.config.quote_size == 20

    def test_should_quote_when_running(self):
        """Test quote conditions when running."""
        strategy = MarketMakerStrategy("AAPL")
        strategy.start()

        snapshot = make_snapshot(bid=150.0, ask=150.10)  # ~6.67 bps spread, within range

        assert strategy._should_quote(snapshot) is True

    def test_should_not_quote_when_stopped(self):
        """Test no quoting when stopped."""
        strategy = MarketMakerStrategy("AAPL")
        strategy.stop()

        snapshot = make_snapshot()

        assert strategy._should_quote(snapshot) is False

    def test_should_not_quote_spread_too_tight(self):
        """Test no quoting when spread too tight."""
        config = MarketMakerConfig(min_book_spread_bps=5.0)
        strategy = MarketMakerStrategy("AAPL", config=config)
        strategy.start()

        snapshot = make_snapshot(bid=150.0, ask=150.01)  # Spread ~0.67 bps

        assert strategy._should_quote(snapshot) is False

    def test_should_not_quote_spread_too_wide(self):
        """Test no quoting when spread too wide."""
        config = MarketMakerConfig(max_book_spread_bps=50.0)
        strategy = MarketMakerStrategy("AAPL", config=config)
        strategy.start()

        snapshot = make_snapshot(bid=150.0, ask=152.0)  # Spread ~132 bps

        assert strategy._should_quote(snapshot) is False

    def test_should_not_quote_at_position_limit(self):
        """Test no quoting at position limit."""
        config = MarketMakerConfig(max_position=100)
        strategy = MarketMakerStrategy("AAPL", config=config)
        strategy.start()
        strategy.position.quantity = 100  # At limit

        snapshot = make_snapshot()

        assert strategy._should_quote(snapshot) is False

    def test_calculate_quote_sizes_balanced(self):
        """Test quote sizes with no position."""
        config = MarketMakerConfig(quote_size=10, max_position=100)
        strategy = MarketMakerStrategy("AAPL", config=config)

        bid_size, ask_size = strategy._calculate_quote_sizes()

        assert bid_size == 10
        assert ask_size == 10

    def test_calculate_quote_sizes_long(self):
        """Test quote sizes when long."""
        config = MarketMakerConfig(quote_size=10, max_position=100)
        strategy = MarketMakerStrategy("AAPL", config=config)
        strategy.position.quantity = 50  # 50% of max position

        bid_size, ask_size = strategy._calculate_quote_sizes()

        # Should reduce bid size when long
        assert bid_size < 10
        assert ask_size == 10

    def test_calculate_quote_sizes_short(self):
        """Test quote sizes when short."""
        config = MarketMakerConfig(quote_size=10, max_position=100)
        strategy = MarketMakerStrategy("AAPL", config=config)
        strategy.position.quantity = -50

        bid_size, ask_size = strategy._calculate_quote_sizes()

        assert bid_size == 10
        assert ask_size < 10  # Should reduce ask size when short

    def test_kill_switch(self):
        """Test kill switch activation."""
        config = MarketMakerConfig(max_daily_loss=100.0)
        strategy = MarketMakerStrategy("AAPL", config=config)
        strategy.start()
        strategy._daily_pnl = -150.0  # Exceed max loss

        strategy._trigger_kill_switch()

        assert strategy._kill_switch_triggered is True
        assert strategy.state == StrategyState.STOPPED
        assert len(strategy.active_orders) == 0

    def test_reset_daily(self):
        """Test daily reset."""
        strategy = MarketMakerStrategy("AAPL")
        strategy._daily_pnl = -500.0
        strategy._kill_switch_triggered = True

        strategy.reset_daily()

        assert strategy._daily_pnl == 0.0
        assert strategy._kill_switch_triggered is False

    def test_get_status(self):
        """Test getting detailed status."""
        config = MarketMakerConfig(spread_bps=15.0)
        strategy = MarketMakerStrategy("AAPL", config=config)
        strategy.start()

        status = strategy.get_status()

        assert status["symbol"] == "AAPL"
        assert status["config"]["spread_bps"] == 15.0
        assert "daily_pnl" in status
        assert "kill_switch" in status
