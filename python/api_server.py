"""WebSocket API server for streaming trading data to the frontend."""

import asyncio
import json
from datetime import datetime
from typing import Any

import structlog
from aiohttp import web, WSMsgType

from python.config import settings
from python.ib_connector import IBConnector, TickData # Keep IBConnector for now, but it's not used by options strategy
from python.orderbook import OrderBookManager
from python.strategy.market_maker import MarketMakerConfig, MarketMakerStrategy
from python.alpaca_connector import AlpacaConnector
from python.strategy.options_hedging_strategy import OptionsHedgingStrategy

logger = structlog.get_logger(__name__)

# Global state
clients: set[web.WebSocketResponse] = set()
orderbook_manager = OrderBookManager()
strategy: MarketMakerStrategy | None = None # Existing Market Maker Strategy
alpaca_connector: AlpacaConnector | None = None # New Alpaca Connector
options_strategy: OptionsHedgingStrategy | None = None # New Options Hedging Strategy
ib_connector: IBConnector | None = None # Existing IB Connector (not used by options strategy)


async def broadcast(message_type: str, data: Any) -> None:
    """Broadcast a message to all connected WebSocket clients."""
    if not clients:
        return

    message = json.dumps({"type": message_type, "data": data})
    disconnected = set()

    for ws in clients:
        try:
            await ws.send_str(message)
        except Exception:
            disconnected.add(ws)

    clients.difference_update(disconnected)


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    """Handle WebSocket connections from the frontend."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    clients.add(ws)

    logger.info("WebSocket client connected", total_clients=len(clients))

    # Send initial state
    if strategy:
        await ws.send_str(
            json.dumps({"type": "strategy_status", "data": strategy.get_status()})
        )
    if options_strategy:
        # You might want to send initial status for options_strategy too
        pass

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    await handle_client_message(ws, data)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from client")
            elif msg.type == WSMsgType.ERROR:
                logger.error("WebSocket error", error=ws.exception())
    finally:
        clients.discard(ws)
        logger.info("WebSocket client disconnected", total_clients=len(clients))

    return ws


async def handle_client_message(ws: web.WebSocketResponse, message: dict) -> None:
    """Handle messages from WebSocket clients."""
    global strategy

    msg_type = message.get("type")
    data = message.get("data", {})

    if msg_type == "strategy_control":
        action = data.get("action")

        if action == "start" and strategy:
            strategy.start()
            await broadcast("strategy_status", strategy.get_status())
        elif action == "pause" and strategy:
            strategy.pause()
            await broadcast("strategy_status", strategy.get_status())
        elif action == "stop" and strategy:
            strategy.stop()
            await broadcast("strategy_status", strategy.get_status())
        elif action == "reset_kill_switch" and strategy:
            strategy.reset_daily()
            await broadcast("strategy_status", strategy.get_status())

    elif msg_type == "subscribe":
        symbol = data.get("symbol", "AAPL")
        logger.info("Client subscribed to symbol", symbol=symbol)


async def demo_data_generator() -> None:
    """Generate demo tick data when IB is not connected."""
    import random

    price = 150.0
    trade_id = 0

    while True:
        # Random walk
        price += random.gauss(0, 0.02)
        price = max(140, min(160, price))

        spread = 0.02 + random.random() * 0.03
        bid = round(price - spread / 2, 2)
        ask = round(price + spread / 2, 2)

        tick = TickData(
            timestamp=datetime.now(),
            symbol="AAPL",
            bid=bid,
            ask=ask,
            bid_size=random.randint(100, 500),
            ask_size=random.randint(100, 500),
            last=round(bid + random.random() * spread, 2),
            volume=random.randint(1000, 10000),
        )

        # Update orderbook
        orderbook_manager.update(tick)

        # Broadcast tick
        await broadcast(
            "tick",
            {
                "timestamp": tick.timestamp.isoformat(),
                "symbol": tick.symbol,
                "bid": tick.bid,
                "ask": tick.ask,
                "bid_size": tick.bid_size,
                "ask_size": tick.ask_size,
                "last": tick.last,
                "volume": tick.volume,
            },
        )

        # Update strategy if running
        if strategy and strategy.state.value == "running":
            snapshot = orderbook_manager.get_book(tick.symbol).snapshot()
            strategy.on_book_update(snapshot)

            # Simulate occasional fills
            if random.random() < 0.05:
                orders = strategy.get_orders()
                for order in orders:
                    if (order.side == "BUY" and random.random() < 0.3) or (
                        order.side == "SELL" and random.random() < 0.3
                    ):
                        fill_price = ask if order.side == "BUY" else bid
                        strategy.on_fill(order, fill_price, order.quantity)

                        trade_id += 1
                        await broadcast(
                            "trade",
                            {
                                "id": f"trade-{trade_id}",
                                "timestamp": datetime.now().isoformat(),
                                "symbol": order.symbol,
                                "side": order.side,
                                "price": fill_price,
                                "quantity": order.quantity,
                            },
                        )

                        await broadcast(
                            "position",
                            {
                                "symbol": strategy.symbol,
                                "quantity": strategy.position.quantity,
                                "avg_cost": strategy.position.avg_cost,
                                "unrealized_pnl": strategy.position.unrealized_pnl,
                                "realized_pnl": strategy.position.realized_pnl,
                            },
                        )

            await broadcast("strategy_status", strategy.get_status())

        await asyncio.sleep(0.2)  # 5 ticks per second


async def run_options_strategy_task(app: web.Application) -> None:
    """Task to periodically run the options hedging strategy."""
    global alpaca_connector, options_strategy
    
    # Initialize Alpaca Connector
    alpaca_connector = AlpacaConnector()
    
    # Initialize Options Hedging Strategy
    options_strategy = OptionsHedgingStrategy(risk_free_rate=alpaca_connector.get_risk_free_rate())

    # Main loop for the options strategy
    while True:
        try:
            # Fetch current stock price (e.g., for SPY as a proxy for underlying)
            # The symbol 'SPY' is used here as an example, it should be configurable
            current_stock_price = alpaca_connector.get_current_price("SPY")
            if current_stock_price is None:
                logger.warning("Could not fetch current stock price for SPY. Skipping options strategy run.")
                await asyncio.sleep(settings.OPTIONS_STRATEGY_INTERVAL_SECONDS)
                continue

            # Fetch options positions (currently mocked)
            options_positions = alpaca_connector.get_options_positions()
            
            # Update risk-free rate (if it can be fetched dynamically)
            # For now, it's hardcoded in AlpacaConnector, but this is where it would be updated
            options_strategy.update_risk_free_rate(alpaca_connector.get_risk_free_rate())

            # Run the strategy
            options_strategy.run_strategy(current_stock_price, options_positions)

        except Exception as e:
            logger.error("Error in options strategy task", error=e)
        
        await asyncio.sleep(settings.OPTIONS_STRATEGY_INTERVAL_SECONDS) # Run every X seconds


async def start_background_tasks(app: web.Application) -> None:
    """Start background tasks when the app starts."""
    global strategy

    # Initialize strategy
    config = MarketMakerConfig(
        spread_bps=10.0,
        quote_size=10,
        max_position=100,
    )
    strategy = MarketMakerStrategy("AAPL", config=config)

    # Start demo data generator
    app["demo_task"] = asyncio.create_task(demo_data_generator())
    logger.info("Started demo data generator")

    # Start options strategy task
    app["options_strategy_task"] = asyncio.create_task(run_options_strategy_task(app))
    logger.info("Started options hedging strategy task")


async def cleanup_background_tasks(app: web.Application) -> None:
    """Clean up background tasks when the app shuts down."""
    if "demo_task" in app:
        app["demo_task"].cancel()
        try:
            await app["demo_task"]
        except asyncio.CancelledError:
            pass
    
    if "options_strategy_task" in app:
        app["options_strategy_task"].cancel()
        try:
            await app["options_strategy_task"]
        except asyncio.CancelledError:
            pass

    # Close all WebSocket connections
    for ws in list(clients):
        await ws.close()


async def health_handler(request: web.Request) -> web.Response:
    """Health check endpoint."""
    return web.json_response({"status": "ok"})


async def status_handler(request: web.Request) -> web.Response:
    """Get current strategy status."""
    if strategy:
        return web.json_response(strategy.get_status())
    return web.json_response({"error": "No strategy initialized"}, status=500)


def create_app() -> web.Application:
    """Create the aiohttp application."""
    app = web.Application()

    # Routes
    app.router.add_get("/ws", websocket_handler)
    app.router.add_get("/api/health", health_handler)
    app.router.add_get("/api/status", status_handler)

    # Lifecycle
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)

    return app


def main() -> None:
    """Run the API server."""
    app = create_app()
    logger.info("Starting API server", host="0.0.0.0", port=8000)
    web.run_app(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
