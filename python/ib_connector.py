"""Interactive Brokers TWS/Gateway connection layer using ib_insync."""

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import structlog
from ib_insync import IB, Contract, Stock, Ticker, util

from python.config import IBSettings, settings

logger = structlog.get_logger(__name__)


@dataclass
class TickData:
    """Normalized tick data structure."""

    timestamp: datetime
    symbol: str
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    last: float
    volume: int

    @classmethod
    def from_ticker(cls, ticker: Ticker) -> "TickData":
        """Create TickData from ib_insync Ticker."""
        return cls(
            timestamp=datetime.now(),
            symbol=ticker.contract.symbol if ticker.contract else "UNKNOWN",
            bid=ticker.bid if ticker.bid and ticker.bid > 0 else 0.0,
            ask=ticker.ask if ticker.ask and ticker.ask > 0 else 0.0,
            bid_size=int(ticker.bidSize) if ticker.bidSize else 0,
            ask_size=int(ticker.askSize) if ticker.askSize else 0,
            last=ticker.last if ticker.last and ticker.last > 0 else 0.0,
            volume=int(ticker.volume) if ticker.volume else 0,
        )


class IBConnector:
    """Async connection manager for Interactive Brokers TWS/Gateway."""

    def __init__(self, config: IBSettings | None = None) -> None:
        self.config = config or settings.ib
        self.ib = IB()
        self._connected = False
        self._subscriptions: dict[str, Ticker] = {}
        self._tick_callbacks: list[Callable[[TickData], None]] = []
        self._reconnect_task: asyncio.Task | None = None
        self._shutdown = False

    @property
    def connected(self) -> bool:
        """Check if connected to IB."""
        return self._connected and self.ib.isConnected()

    async def connect(self) -> None:
        """Establish connection to IB TWS/Gateway."""
        if self.connected:
            logger.info("Already connected to IB")
            return

        logger.info(
            "Connecting to IB",
            host=self.config.host,
            port=self.config.port,
            client_id=self.config.client_id,
        )

        try:
            await asyncio.wait_for(
                self.ib.connectAsync(
                    host=self.config.host,
                    port=self.config.port,
                    clientId=self.config.client_id,
                    readonly=self.config.readonly,
                ),
                timeout=self.config.timeout,
            )
            self._connected = True
            self.ib.disconnectedEvent += self._on_disconnect
            logger.info("Connected to IB", account=self.ib.managedAccounts())
        except asyncio.TimeoutError:
            logger.error("Connection timeout", timeout=self.config.timeout)
            raise
        except Exception as e:
            logger.error("Connection failed", error=str(e))
            raise

    def _on_disconnect(self) -> None:
        """Handle disconnection events."""
        self._connected = False
        logger.warning("Disconnected from IB")

        if not self._shutdown:
            self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        backoff = 1.0
        max_backoff = 60.0

        while not self._shutdown and not self.connected:
            logger.info("Attempting reconnection", backoff=backoff)
            try:
                await self.connect()
                # Resubscribe to all symbols
                for symbol in list(self._subscriptions.keys()):
                    await self.subscribe(symbol)
                logger.info("Reconnection successful")
                return
            except Exception as e:
                logger.warning("Reconnection failed", error=str(e), next_attempt=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    async def disconnect(self) -> None:
        """Gracefully disconnect from IB."""
        self._shutdown = True

        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        # Unsubscribe all
        for symbol in list(self._subscriptions.keys()):
            await self.unsubscribe(symbol)

        if self.ib.isConnected():
            self.ib.disconnect()

        self._connected = False
        logger.info("Disconnected from IB")

    def _create_contract(self, symbol: str) -> Contract:
        """Create a stock contract for US equities."""
        return Stock(symbol, "SMART", "USD")

    async def subscribe(self, symbol: str) -> Ticker:
        """Subscribe to market data for a symbol."""
        if symbol in self._subscriptions:
            logger.debug("Already subscribed", symbol=symbol)
            return self._subscriptions[symbol]

        if not self.connected:
            raise RuntimeError("Not connected to IB")

        contract = self._create_contract(symbol)
        await self.ib.qualifyContractsAsync(contract)

        ticker = self.ib.reqMktData(contract, "", False, False)
        ticker.updateEvent += lambda t: self._on_tick_update(t)
        self._subscriptions[symbol] = ticker

        logger.info("Subscribed to market data", symbol=symbol)
        return ticker

    async def unsubscribe(self, symbol: str) -> None:
        """Unsubscribe from market data."""
        if symbol not in self._subscriptions:
            return

        ticker = self._subscriptions.pop(symbol)
        if ticker.contract:
            self.ib.cancelMktData(ticker.contract)
        logger.info("Unsubscribed from market data", symbol=symbol)

    def _on_tick_update(self, ticker: Ticker) -> None:
        """Handle tick updates from IB."""
        tick_data = TickData.from_ticker(ticker)

        for callback in self._tick_callbacks:
            try:
                callback(tick_data)
            except Exception as e:
                logger.error("Tick callback error", error=str(e))

    def add_tick_callback(self, callback: Callable[[TickData], None]) -> None:
        """Register a callback for tick updates."""
        self._tick_callbacks.append(callback)

    def remove_tick_callback(self, callback: Callable[[TickData], None]) -> None:
        """Remove a tick callback."""
        if callback in self._tick_callbacks:
            self._tick_callbacks.remove(callback)

    async def tick_stream(self, symbols: list[str]) -> AsyncGenerator[TickData, None]:
        """Async generator that yields tick data for subscribed symbols."""
        queue: asyncio.Queue[TickData] = asyncio.Queue()

        def enqueue_tick(tick: TickData) -> None:
            try:
                queue.put_nowait(tick)
            except asyncio.QueueFull:
                pass  # Drop ticks if queue is full

        self.add_tick_callback(enqueue_tick)

        try:
            # Subscribe to all symbols
            for symbol in symbols:
                await self.subscribe(symbol)

            # Yield ticks as they arrive
            while not self._shutdown:
                try:
                    tick = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield tick
                except asyncio.TimeoutError:
                    # Allow checking shutdown condition
                    continue
        finally:
            self.remove_tick_callback(enqueue_tick)

    async def get_positions(self) -> list[dict]:
        """Get current positions."""
        if not self.connected:
            raise RuntimeError("Not connected to IB")

        positions = await self.ib.reqPositionsAsync()
        return [
            {
                "symbol": p.contract.symbol,
                "position": p.position,
                "avg_cost": p.avgCost,
                "account": p.account,
            }
            for p in positions
        ]

    async def get_account_summary(self) -> dict:
        """Get account summary."""
        if not self.connected:
            raise RuntimeError("Not connected to IB")

        summary = await self.ib.reqAccountSummaryAsync()
        return {item.tag: item.value for item in summary}


async def main() -> None:
    """Example usage of IBConnector."""
    util.startLoop()  # Enable asyncio event loop for ib_insync

    connector = IBConnector()

    try:
        await connector.connect()

        # Stream ticks for configured symbols
        async for tick in connector.tick_stream(settings.symbols):
            logger.info(
                "Tick received",
                symbol=tick.symbol,
                bid=tick.bid,
                ask=tick.ask,
                last=tick.last,
            )
    except KeyboardInterrupt:
        pass
    finally:
        await connector.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
