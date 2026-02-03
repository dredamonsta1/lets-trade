"""Tests for IB connector module."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from python.config import IBSettings
from python.ib_connector import IBConnector, TickData


class TestTickData:
    """Tests for TickData class."""

    def test_from_ticker_with_valid_data(self):
        """Test creating TickData from a valid ticker."""
        mock_ticker = MagicMock()
        mock_ticker.contract.symbol = "AAPL"
        mock_ticker.bid = 150.25
        mock_ticker.ask = 150.30
        mock_ticker.bidSize = 100
        mock_ticker.askSize = 200
        mock_ticker.last = 150.27
        mock_ticker.volume = 1000000

        tick = TickData.from_ticker(mock_ticker)

        assert tick.symbol == "AAPL"
        assert tick.bid == 150.25
        assert tick.ask == 150.30
        assert tick.bid_size == 100
        assert tick.ask_size == 200
        assert tick.last == 150.27
        assert tick.volume == 1000000
        assert isinstance(tick.timestamp, datetime)

    def test_from_ticker_with_missing_data(self):
        """Test creating TickData when some fields are missing/invalid."""
        mock_ticker = MagicMock()
        mock_ticker.contract.symbol = "AAPL"
        mock_ticker.bid = -1  # Invalid
        mock_ticker.ask = None  # Missing
        mock_ticker.bidSize = None
        mock_ticker.askSize = None
        mock_ticker.last = 0  # Zero
        mock_ticker.volume = None

        tick = TickData.from_ticker(mock_ticker)

        assert tick.symbol == "AAPL"
        assert tick.bid == 0.0
        assert tick.ask == 0.0
        assert tick.bid_size == 0
        assert tick.ask_size == 0
        assert tick.last == 0.0
        assert tick.volume == 0


class TestIBConnector:
    """Tests for IBConnector class."""

    def test_init_with_default_config(self):
        """Test connector initialization with default settings."""
        connector = IBConnector()

        assert connector.config.host == "127.0.0.1"
        assert connector.config.port == 7497
        assert connector.config.client_id == 1
        assert not connector.connected

    def test_init_with_custom_config(self):
        """Test connector initialization with custom settings."""
        config = IBSettings(
            host="192.168.1.100",
            port=7496,
            client_id=99,
        )
        connector = IBConnector(config=config)

        assert connector.config.host == "192.168.1.100"
        assert connector.config.port == 7496
        assert connector.config.client_id == 99

    @pytest.mark.asyncio
    async def test_connect_success(self):
        """Test successful connection to IB."""
        connector = IBConnector()

        with patch.object(connector.ib, "connectAsync", new_callable=AsyncMock) as mock_connect:
            with patch.object(connector.ib, "managedAccounts", return_value=["DU12345"]):
                await connector.connect()

                mock_connect.assert_called_once()
                assert connector._connected is True

    @pytest.mark.asyncio
    async def test_connect_timeout(self):
        """Test connection timeout handling."""
        connector = IBConnector()
        connector.config.timeout = 0.1  # Very short timeout

        async def slow_connect(*args, **kwargs):
            import asyncio

            await asyncio.sleep(1)

        with patch.object(connector.ib, "connectAsync", side_effect=slow_connect):
            with pytest.raises(TimeoutError):
                await connector.connect()

    @pytest.mark.asyncio
    async def test_disconnect(self):
        """Test disconnection from IB."""
        connector = IBConnector()
        connector._connected = True
        connector._shutdown = False

        with patch.object(connector.ib, "isConnected", return_value=True):
            with patch.object(connector.ib, "disconnect") as mock_disconnect:
                await connector.disconnect()

                mock_disconnect.assert_called_once()
                assert connector._connected is False
                assert connector._shutdown is True

    def test_add_tick_callback(self):
        """Test adding tick callbacks."""
        connector = IBConnector()
        callback = MagicMock()

        connector.add_tick_callback(callback)

        assert callback in connector._tick_callbacks

    def test_remove_tick_callback(self):
        """Test removing tick callbacks."""
        connector = IBConnector()
        callback = MagicMock()
        connector._tick_callbacks.append(callback)

        connector.remove_tick_callback(callback)

        assert callback not in connector._tick_callbacks

    def test_on_tick_update_calls_callbacks(self):
        """Test that tick updates call registered callbacks."""
        connector = IBConnector()
        callback = MagicMock()
        connector.add_tick_callback(callback)

        mock_ticker = MagicMock()
        mock_ticker.contract.symbol = "AAPL"
        mock_ticker.bid = 150.0
        mock_ticker.ask = 150.10
        mock_ticker.bidSize = 100
        mock_ticker.askSize = 100
        mock_ticker.last = 150.05
        mock_ticker.volume = 1000

        connector._on_tick_update(mock_ticker)

        callback.assert_called_once()
        tick_arg = callback.call_args[0][0]
        assert isinstance(tick_arg, TickData)
        assert tick_arg.symbol == "AAPL"

    def test_on_tick_update_handles_callback_errors(self):
        """Test that errors in callbacks don't crash the connector."""
        connector = IBConnector()
        bad_callback = MagicMock(side_effect=Exception("Callback error"))
        good_callback = MagicMock()
        connector.add_tick_callback(bad_callback)
        connector.add_tick_callback(good_callback)

        mock_ticker = MagicMock()
        mock_ticker.contract.symbol = "AAPL"
        mock_ticker.bid = 150.0
        mock_ticker.ask = 150.10
        mock_ticker.bidSize = 100
        mock_ticker.askSize = 100
        mock_ticker.last = 150.05
        mock_ticker.volume = 1000

        # Should not raise
        connector._on_tick_update(mock_ticker)

        # Good callback should still be called
        good_callback.assert_called_once()
