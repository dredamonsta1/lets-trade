"""Abstract base class for trading strategies."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import structlog

from python.orderbook import OrderBookSnapshot

logger = structlog.get_logger(__name__)


class StrategyState(Enum):
    """Strategy lifecycle states."""

    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class Order:
    """Represents a trading order."""

    id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: int
    price: float
    order_type: str = "LMT"
    status: str = "pending"
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class Position:
    """Represents a trading position."""

    symbol: str
    quantity: int  # Positive = long, negative = short
    avg_cost: float
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0

    @property
    def market_value(self) -> float:
        """Calculate market value at average cost."""
        return abs(self.quantity) * self.avg_cost

    def update_unrealized_pnl(self, current_price: float) -> None:
        """Update unrealized PnL based on current price."""
        if self.quantity != 0:
            self.unrealized_pnl = (current_price - self.avg_cost) * self.quantity


@dataclass
class StrategyMetrics:
    """Performance metrics for a strategy."""

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    start_time: datetime = field(default_factory=datetime.now)

    @property
    def win_rate(self) -> float:
        """Calculate win rate percentage."""
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100


class Strategy(ABC):
    """
    Abstract base class for all trading strategies.

    Subclasses must implement:
    - on_book_update(): React to order book changes
    - get_orders(): Return orders to place
    """

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.state = StrategyState.INITIALIZING
        self.position = Position(symbol=symbol, quantity=0, avg_cost=0.0)
        self.metrics = StrategyMetrics()
        self.active_orders: dict[str, Order] = {}
        self._order_counter = 0

    def _generate_order_id(self) -> str:
        """Generate unique order ID."""
        self._order_counter += 1
        return f"{self.symbol}-{self._order_counter}-{datetime.now().strftime('%H%M%S%f')}"

    def start(self) -> None:
        """Start the strategy."""
        logger.info("Starting strategy", symbol=self.symbol, strategy=self.__class__.__name__)
        self.state = StrategyState.RUNNING

    def stop(self) -> None:
        """Stop the strategy."""
        logger.info("Stopping strategy", symbol=self.symbol, strategy=self.__class__.__name__)
        self.state = StrategyState.STOPPED

    def pause(self) -> None:
        """Pause the strategy."""
        logger.info("Pausing strategy", symbol=self.symbol)
        self.state = StrategyState.PAUSED

    def resume(self) -> None:
        """Resume the strategy."""
        logger.info("Resuming strategy", symbol=self.symbol)
        self.state = StrategyState.RUNNING

    @abstractmethod
    def on_book_update(self, snapshot: OrderBookSnapshot) -> None:
        """
        Called when the order book updates.

        Args:
            snapshot: Current order book state
        """
        pass

    @abstractmethod
    def get_orders(self) -> list[Order]:
        """
        Get orders to place based on current strategy state.

        Returns:
            List of orders to submit
        """
        pass

    def on_fill(self, order: Order, fill_price: float, fill_qty: int) -> None:
        """
        Called when an order is filled.

        Args:
            order: The filled order
            fill_price: Execution price
            fill_qty: Quantity filled
        """
        logger.info(
            "Order filled",
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            price=fill_price,
            qty=fill_qty,
        )

        # Update position
        if order.side == "BUY":
            new_qty = self.position.quantity + fill_qty
            if self.position.quantity >= 0:
                # Adding to long or opening long
                total_cost = (self.position.quantity * self.position.avg_cost) + (
                    fill_qty * fill_price
                )
                self.position.avg_cost = total_cost / new_qty if new_qty > 0 else 0.0
            self.position.quantity = new_qty
        else:  # SELL
            new_qty = self.position.quantity - fill_qty
            if self.position.quantity <= 0:
                # Adding to short or opening short
                total_cost = (abs(self.position.quantity) * self.position.avg_cost) + (
                    fill_qty * fill_price
                )
                self.position.avg_cost = total_cost / abs(new_qty) if new_qty < 0 else 0.0
            self.position.quantity = new_qty

        # Update metrics
        self.metrics.total_trades += 1

    def on_cancel(self, order: Order) -> None:
        """Called when an order is cancelled."""
        logger.info("Order cancelled", order_id=order.id, symbol=order.symbol)
        if order.id in self.active_orders:
            del self.active_orders[order.id]

    def get_status(self) -> dict[str, Any]:
        """Get strategy status summary."""
        return {
            "symbol": self.symbol,
            "state": self.state.value,
            "position": self.position.quantity,
            "avg_cost": self.position.avg_cost,
            "unrealized_pnl": self.position.unrealized_pnl,
            "realized_pnl": self.position.realized_pnl,
            "active_orders": len(self.active_orders),
            "total_trades": self.metrics.total_trades,
        }
