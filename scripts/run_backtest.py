#!/usr/bin/env python3
"""Run backtests using historical data from QuestDB."""

import argparse
import asyncio
from datetime import datetime, timedelta

import polars as pl
import structlog

from python.config import settings
from python.orderbook import OrderBook, OrderBookSnapshot
from python.strategy.market_maker import MarketMakerConfig, MarketMakerStrategy

logger = structlog.get_logger(__name__)


async def fetch_historical_data(
    symbol: str,
    start_time: datetime,
    end_time: datetime,
) -> pl.DataFrame:
    """
    Fetch historical tick data from QuestDB.

    Note: This requires QuestDB to be running and populated with data.
    For initial testing, you can use synthetic data.
    """
    import questdb.ingress as qi

    query = f"""
    SELECT timestamp, symbol, bid, ask, bid_size, ask_size, last, volume
    FROM ticks
    WHERE symbol = '{symbol}'
      AND timestamp >= '{start_time.isoformat()}'
      AND timestamp <= '{end_time.isoformat()}'
    ORDER BY timestamp
    """

    # Connect to QuestDB via PostgreSQL wire protocol
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=settings.questdb.host,
            port=settings.questdb.pg_port,
            user="admin",
            password="quest",
            database="qdb",
        )
        df = pl.read_database(query, conn)
        conn.close()
        return df
    except Exception as e:
        logger.warning("Could not fetch from QuestDB, using synthetic data", error=str(e))
        return generate_synthetic_data(symbol, start_time, end_time)


def generate_synthetic_data(
    symbol: str,
    start_time: datetime,
    end_time: datetime,
    tick_interval_ms: int = 100,
) -> pl.DataFrame:
    """Generate synthetic tick data for backtesting."""
    import random

    random.seed(42)

    base_price = 150.0
    spread = 0.05
    volatility = 0.0001

    timestamps = []
    bids = []
    asks = []
    bid_sizes = []
    ask_sizes = []
    lasts = []
    volumes = []

    current_time = start_time
    current_price = base_price

    while current_time < end_time:
        # Random walk for price
        current_price += random.gauss(0, current_price * volatility)

        bid = round(current_price - spread / 2, 2)
        ask = round(current_price + spread / 2, 2)
        bid_size = random.randint(50, 500)
        ask_size = random.randint(50, 500)
        last = round(random.uniform(bid, ask), 2)
        volume = random.randint(100, 1000)

        timestamps.append(current_time)
        bids.append(bid)
        asks.append(ask)
        bid_sizes.append(bid_size)
        ask_sizes.append(ask_size)
        lasts.append(last)
        volumes.append(volume)

        current_time += timedelta(milliseconds=tick_interval_ms)

    return pl.DataFrame(
        {
            "timestamp": timestamps,
            "symbol": [symbol] * len(timestamps),
            "bid": bids,
            "ask": asks,
            "bid_size": bid_sizes,
            "ask_size": ask_sizes,
            "last": lasts,
            "volume": volumes,
        }
    )


class BacktestEngine:
    """Simple backtest engine for strategy evaluation."""

    def __init__(self, strategy: MarketMakerStrategy) -> None:
        self.strategy = strategy
        self.trades: list[dict] = []
        self.snapshots: list[dict] = []

    def run(self, data: pl.DataFrame) -> dict:
        """Run backtest on historical data."""
        logger.info(
            "Starting backtest",
            symbol=self.strategy.symbol,
            ticks=len(data),
        )

        self.strategy.start()

        for row in data.iter_rows(named=True):
            snapshot = OrderBookSnapshot(
                symbol=row["symbol"],
                timestamp=row["timestamp"],
                bid=row["bid"],
                ask=row["ask"],
                bid_size=row["bid_size"],
                ask_size=row["ask_size"],
                mid=(row["bid"] + row["ask"]) / 2,
                spread=row["ask"] - row["bid"],
                spread_bps=((row["ask"] - row["bid"]) / ((row["bid"] + row["ask"]) / 2)) * 10000,
                imbalance=(row["bid_size"] - row["ask_size"])
                / (row["bid_size"] + row["ask_size"]),
            )

            self.strategy.on_book_update(snapshot)

            # Simulate fills (simplified)
            orders = self.strategy.get_orders()
            for order in orders:
                if order.side == "BUY" and order.price >= snapshot.ask:
                    # Buy order would be filled
                    self.strategy.on_fill(order, snapshot.ask, order.quantity)
                    self.trades.append(
                        {
                            "timestamp": snapshot.timestamp,
                            "side": "BUY",
                            "price": snapshot.ask,
                            "quantity": order.quantity,
                        }
                    )
                elif order.side == "SELL" and order.price <= snapshot.bid:
                    # Sell order would be filled
                    self.strategy.on_fill(order, snapshot.bid, order.quantity)
                    self.trades.append(
                        {
                            "timestamp": snapshot.timestamp,
                            "side": "SELL",
                            "price": snapshot.bid,
                            "quantity": order.quantity,
                        }
                    )

            # Record snapshot
            self.snapshots.append(
                {
                    "timestamp": snapshot.timestamp,
                    "mid": snapshot.mid,
                    "position": self.strategy.position.quantity,
                    "unrealized_pnl": self.strategy.position.unrealized_pnl,
                }
            )

        self.strategy.stop()

        return self._calculate_results()

    def _calculate_results(self) -> dict:
        """Calculate backtest results."""
        if not self.trades:
            return {
                "total_trades": 0,
                "total_pnl": 0.0,
                "final_position": self.strategy.position.quantity,
            }

        trades_df = pl.DataFrame(self.trades)

        total_bought = trades_df.filter(pl.col("side") == "BUY").select(
            (pl.col("price") * pl.col("quantity")).sum()
        )[0, 0]
        total_sold = trades_df.filter(pl.col("side") == "SELL").select(
            (pl.col("price") * pl.col("quantity")).sum()
        )[0, 0]

        total_bought = total_bought or 0
        total_sold = total_sold or 0

        realized_pnl = total_sold - total_bought

        return {
            "total_trades": len(self.trades),
            "buy_trades": len(trades_df.filter(pl.col("side") == "BUY")),
            "sell_trades": len(trades_df.filter(pl.col("side") == "SELL")),
            "realized_pnl": realized_pnl,
            "unrealized_pnl": self.strategy.position.unrealized_pnl,
            "total_pnl": realized_pnl + self.strategy.position.unrealized_pnl,
            "final_position": self.strategy.position.quantity,
            "avg_position": sum(s["position"] for s in self.snapshots) / len(self.snapshots)
            if self.snapshots
            else 0,
        }


async def main() -> None:
    """Run backtest from command line."""
    parser = argparse.ArgumentParser(description="Run trading strategy backtest")
    parser.add_argument("--symbol", default="AAPL", help="Symbol to backtest")
    parser.add_argument("--days", type=int, default=1, help="Number of days to backtest")
    parser.add_argument("--spread-bps", type=float, default=10.0, help="Target spread in bps")
    parser.add_argument("--quote-size", type=int, default=10, help="Quote size")
    parser.add_argument("--max-position", type=int, default=100, help="Max position size")
    args = parser.parse_args()

    # Configure strategy
    config = MarketMakerConfig(
        spread_bps=args.spread_bps,
        quote_size=args.quote_size,
        max_position=args.max_position,
    )

    strategy = MarketMakerStrategy(args.symbol, config=config)

    # Fetch or generate data
    end_time = datetime.now()
    start_time = end_time - timedelta(days=args.days)

    logger.info(
        "Fetching historical data",
        symbol=args.symbol,
        start=start_time.isoformat(),
        end=end_time.isoformat(),
    )

    data = await fetch_historical_data(args.symbol, start_time, end_time)

    logger.info("Data loaded", rows=len(data))

    # Run backtest
    engine = BacktestEngine(strategy)
    results = engine.run(data)

    # Print results
    print("\n" + "=" * 50)
    print("BACKTEST RESULTS")
    print("=" * 50)
    print(f"Symbol:          {args.symbol}")
    print(f"Total Trades:    {results['total_trades']}")
    print(f"  Buy Trades:    {results.get('buy_trades', 0)}")
    print(f"  Sell Trades:   {results.get('sell_trades', 0)}")
    print(f"Realized PnL:    ${results.get('realized_pnl', 0):.2f}")
    print(f"Unrealized PnL:  ${results.get('unrealized_pnl', 0):.2f}")
    print(f"Total PnL:       ${results.get('total_pnl', 0):.2f}")
    print(f"Final Position:  {results['final_position']}")
    print(f"Avg Position:    {results.get('avg_position', 0):.1f}")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
