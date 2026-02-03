"""Data ingestion pipeline: IB ticks → Redis (pub/sub) → QuestDB (storage)."""

import asyncio
import json
from datetime import datetime

import redis.asyncio as redis
import structlog
from questdb.ingress import Sender, TimestampNanos

from python.config import QuestDBSettings, RedisSettings, settings
from python.ib_connector import IBConnector, TickData

logger = structlog.get_logger(__name__)

# QuestDB table schema (created automatically by ILP)
TICK_TABLE = "ticks"
REDIS_TICK_CHANNEL = "ticks"


class QuestDBWriter:
    """High-performance writer to QuestDB using InfluxDB Line Protocol."""

    def __init__(self, config: QuestDBSettings | None = None) -> None:
        self.config = config or settings.questdb
        self._sender: Sender | None = None
        self._buffer_count = 0
        self._flush_interval = 100  # Flush every N ticks
        self._last_flush = datetime.now()

    async def connect(self) -> None:
        """Initialize QuestDB sender."""
        self._sender = Sender(
            host=self.config.host,
            port=self.config.ilp_port,
        )
        logger.info(
            "QuestDB writer initialized",
            host=self.config.host,
            port=self.config.ilp_port,
        )

    async def disconnect(self) -> None:
        """Flush and close QuestDB sender."""
        if self._sender:
            try:
                self._sender.flush()
            except Exception as e:
                logger.error("Error flushing QuestDB", error=str(e))
            finally:
                self._sender.close()
                self._sender = None
        logger.info("QuestDB writer closed")

    def write_tick(self, tick: TickData) -> None:
        """Write a single tick to QuestDB buffer."""
        if not self._sender:
            raise RuntimeError("QuestDB writer not connected")

        try:
            self._sender.row(
                TICK_TABLE,
                symbols={"symbol": tick.symbol},
                columns={
                    "bid": tick.bid,
                    "ask": tick.ask,
                    "bid_size": tick.bid_size,
                    "ask_size": tick.ask_size,
                    "last": tick.last,
                    "volume": tick.volume,
                },
                at=TimestampNanos(int(tick.timestamp.timestamp() * 1e9)),
            )
            self._buffer_count += 1

            # Auto-flush based on count or time
            if self._buffer_count >= self._flush_interval:
                self._flush()
        except Exception as e:
            logger.error("Error writing tick to QuestDB", error=str(e), symbol=tick.symbol)

    def _flush(self) -> None:
        """Flush buffered data to QuestDB."""
        if self._sender and self._buffer_count > 0:
            try:
                self._sender.flush()
                logger.debug("Flushed ticks to QuestDB", count=self._buffer_count)
                self._buffer_count = 0
                self._last_flush = datetime.now()
            except Exception as e:
                logger.error("Error flushing to QuestDB", error=str(e))


class RedisPublisher:
    """Publish tick data to Redis for real-time consumers."""

    def __init__(self, config: RedisSettings | None = None) -> None:
        self.config = config or settings.redis
        self._client: redis.Redis | None = None

    async def connect(self) -> None:
        """Connect to Redis."""
        self._client = redis.Redis(
            host=self.config.host,
            port=self.config.port,
            db=self.config.db,
            decode_responses=True,
        )
        # Test connection
        await self._client.ping()
        logger.info("Redis publisher connected", host=self.config.host, port=self.config.port)

    async def disconnect(self) -> None:
        """Close Redis connection."""
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("Redis publisher disconnected")

    async def publish_tick(self, tick: TickData) -> None:
        """Publish tick to Redis channel."""
        if not self._client:
            raise RuntimeError("Redis not connected")

        try:
            message = json.dumps(
                {
                    "timestamp": tick.timestamp.isoformat(),
                    "symbol": tick.symbol,
                    "bid": tick.bid,
                    "ask": tick.ask,
                    "bid_size": tick.bid_size,
                    "ask_size": tick.ask_size,
                    "last": tick.last,
                    "volume": tick.volume,
                }
            )
            await self._client.publish(REDIS_TICK_CHANNEL, message)
        except Exception as e:
            logger.error("Error publishing to Redis", error=str(e), symbol=tick.symbol)


class DataIngestionPipeline:
    """Orchestrates data flow from IB → Redis + QuestDB."""

    def __init__(
        self,
        ib_connector: IBConnector | None = None,
        questdb_writer: QuestDBWriter | None = None,
        redis_publisher: RedisPublisher | None = None,
    ) -> None:
        self.ib = ib_connector or IBConnector()
        self.questdb = questdb_writer or QuestDBWriter()
        self.redis = redis_publisher or RedisPublisher()
        self._running = False
        self._tick_count = 0
        self._start_time: datetime | None = None

    async def start(self, symbols: list[str] | None = None) -> None:
        """Start the data ingestion pipeline."""
        symbols = symbols or settings.symbols

        logger.info("Starting data ingestion pipeline", symbols=symbols)
        self._running = True
        self._start_time = datetime.now()

        # Connect all components
        await self.ib.connect()
        await self.questdb.connect()
        await self.redis.connect()

        try:
            async for tick in self.ib.tick_stream(symbols):
                if not self._running:
                    break

                # Write to QuestDB (batched for performance)
                self.questdb.write_tick(tick)

                # Publish to Redis (real-time)
                await self.redis.publish_tick(tick)

                self._tick_count += 1

                if self._tick_count % 1000 == 0:
                    elapsed = (datetime.now() - self._start_time).total_seconds()
                    rate = self._tick_count / elapsed if elapsed > 0 else 0
                    logger.info(
                        "Pipeline stats",
                        ticks_processed=self._tick_count,
                        elapsed_seconds=round(elapsed, 1),
                        rate_per_second=round(rate, 1),
                    )
        except Exception as e:
            logger.error("Pipeline error", error=str(e))
            raise
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the data ingestion pipeline."""
        logger.info("Stopping data ingestion pipeline", total_ticks=self._tick_count)
        self._running = False

        await self.ib.disconnect()
        await self.questdb.disconnect()
        await self.redis.disconnect()


async def main() -> None:
    """Run the data ingestion pipeline."""
    from ib_insync import util

    util.startLoop()

    pipeline = DataIngestionPipeline()

    try:
        await pipeline.start()
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    finally:
        await pipeline.stop()


if __name__ == "__main__":
    asyncio.run(main())
