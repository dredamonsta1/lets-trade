"""Tests for data ingestion module."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from python.config import QuestDBSettings, RedisSettings
from python.data_ingestion import QuestDBWriter, RedisPublisher
from python.ib_connector import TickData


def make_tick(
    symbol: str = "AAPL",
    bid: float = 150.0,
    ask: float = 150.10,
    last: float = 150.05,
) -> TickData:
    """Helper to create tick data."""
    return TickData(
        timestamp=datetime.now(),
        symbol=symbol,
        bid=bid,
        ask=ask,
        bid_size=100,
        ask_size=100,
        last=last,
        volume=1000,
    )


class TestQuestDBWriter:
    """Tests for QuestDB writer."""

    def test_init_with_default_config(self):
        """Test writer initialization."""
        writer = QuestDBWriter()

        assert writer.config.host == "127.0.0.1"
        assert writer.config.ilp_port == 9009
        assert writer._sender is None

    def test_init_with_custom_config(self):
        """Test writer with custom config."""
        config = QuestDBSettings(host="questdb.local", ilp_port=19009)
        writer = QuestDBWriter(config=config)

        assert writer.config.host == "questdb.local"
        assert writer.config.ilp_port == 19009

    @pytest.mark.asyncio
    async def test_connect_creates_sender(self):
        """Test that connect initializes the sender."""
        writer = QuestDBWriter()

        with patch("python.data_ingestion.Sender") as mock_sender_class:
            mock_sender = MagicMock()
            mock_sender_class.return_value = mock_sender

            await writer.connect()

            mock_sender_class.assert_called_once_with(
                host=writer.config.host,
                port=writer.config.ilp_port,
            )
            assert writer._sender is not None

    @pytest.mark.asyncio
    async def test_disconnect_flushes_and_closes(self):
        """Test that disconnect flushes and closes sender."""
        writer = QuestDBWriter()
        mock_sender = MagicMock()
        writer._sender = mock_sender

        await writer.disconnect()

        mock_sender.flush.assert_called_once()
        mock_sender.close.assert_called_once()
        assert writer._sender is None

    def test_write_tick_buffers_data(self):
        """Test writing a tick to the buffer."""
        writer = QuestDBWriter()
        mock_sender = MagicMock()
        writer._sender = mock_sender
        writer._flush_interval = 1000  # Don't auto-flush

        tick = make_tick()
        writer.write_tick(tick)

        mock_sender.row.assert_called_once()
        call_kwargs = mock_sender.row.call_args
        assert call_kwargs[0][0] == "ticks"  # Table name
        assert call_kwargs[1]["symbols"]["symbol"] == "AAPL"
        assert call_kwargs[1]["columns"]["bid"] == 150.0
        assert call_kwargs[1]["columns"]["ask"] == 150.10

    def test_write_tick_auto_flushes(self):
        """Test that writing flushes when buffer is full."""
        writer = QuestDBWriter()
        mock_sender = MagicMock()
        writer._sender = mock_sender
        writer._flush_interval = 2  # Flush after 2 ticks

        tick = make_tick()
        writer.write_tick(tick)
        assert writer._buffer_count == 1
        mock_sender.flush.assert_not_called()

        writer.write_tick(tick)
        assert writer._buffer_count == 0  # Reset after flush
        mock_sender.flush.assert_called_once()

    def test_write_tick_raises_without_connection(self):
        """Test that writing without connection raises error."""
        writer = QuestDBWriter()

        with pytest.raises(RuntimeError, match="not connected"):
            writer.write_tick(make_tick())


class TestRedisPublisher:
    """Tests for Redis publisher."""

    def test_init_with_default_config(self):
        """Test publisher initialization."""
        publisher = RedisPublisher()

        assert publisher.config.host == "127.0.0.1"
        assert publisher.config.port == 6379
        assert publisher._client is None

    @pytest.mark.asyncio
    async def test_connect_creates_client(self):
        """Test that connect creates Redis client."""
        publisher = RedisPublisher()

        with patch("python.data_ingestion.redis.Redis") as mock_redis_class:
            mock_client = AsyncMock()
            mock_redis_class.return_value = mock_client

            await publisher.connect()

            mock_redis_class.assert_called_once()
            mock_client.ping.assert_called_once()
            assert publisher._client is not None

    @pytest.mark.asyncio
    async def test_disconnect_closes_client(self):
        """Test that disconnect closes Redis client."""
        publisher = RedisPublisher()
        mock_client = AsyncMock()
        publisher._client = mock_client

        await publisher.disconnect()

        mock_client.aclose.assert_called_once()
        assert publisher._client is None

    @pytest.mark.asyncio
    async def test_publish_tick_sends_json(self):
        """Test publishing tick data to Redis."""
        publisher = RedisPublisher()
        mock_client = AsyncMock()
        publisher._client = mock_client

        tick = make_tick()
        await publisher.publish_tick(tick)

        mock_client.publish.assert_called_once()
        call_args = mock_client.publish.call_args
        assert call_args[0][0] == "ticks"  # Channel name

        import json

        message = json.loads(call_args[0][1])
        assert message["symbol"] == "AAPL"
        assert message["bid"] == 150.0
        assert message["ask"] == 150.10

    @pytest.mark.asyncio
    async def test_publish_tick_raises_without_connection(self):
        """Test that publishing without connection raises error."""
        publisher = RedisPublisher()

        with pytest.raises(RuntimeError, match="not connected"):
            await publisher.publish_tick(make_tick())
