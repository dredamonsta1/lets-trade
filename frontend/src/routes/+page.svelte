<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { connectWebSocket, disconnectWebSocket } from '$lib/stores/websocket';
	import {
		connected,
		currentTick,
		orderBook,
		metrics,
		strategyStatus,
		updateFromTick,
		addTrade,
		updateStrategyStatus,
		updatePosition
	} from '$lib/stores/trading';

	import PriceChart from '$lib/components/PriceChart.svelte';
	import OrderBook from '$lib/components/OrderBook.svelte';
	import PositionPanel from '$lib/components/PositionPanel.svelte';
	import TradeHistory from '$lib/components/TradeHistory.svelte';
	import StrategyControls from '$lib/components/StrategyControls.svelte';
	import MetricCard from '$lib/components/MetricCard.svelte';

	let demoMode = true;
	let demoInterval: ReturnType<typeof setInterval>;

	onMount(() => {
		// Try to connect to WebSocket
		connectWebSocket();

		// Start demo mode with simulated data
		startDemoMode();
	});

	onDestroy(() => {
		disconnectWebSocket();
		if (demoInterval) clearInterval(demoInterval);
	});

	function startDemoMode() {
		let price = 150.0;
		let position = 0;
		let tradeId = 0;
		let totalPnl = 0;

		demoInterval = setInterval(() => {
			// Random walk price
			price += (Math.random() - 0.5) * 0.1;
			price = Math.max(140, Math.min(160, price));

			const spread = 0.02 + Math.random() * 0.03;
			const bid = Math.round((price - spread / 2) * 100) / 100;
			const ask = Math.round((price + spread / 2) * 100) / 100;

			// Update tick
			updateFromTick({
				timestamp: new Date().toISOString(),
				symbol: 'AAPL',
				bid,
				ask,
				bid_size: Math.floor(100 + Math.random() * 400),
				ask_size: Math.floor(100 + Math.random() * 400),
				last: Math.round((bid + (ask - bid) * Math.random()) * 100) / 100,
				volume: Math.floor(Math.random() * 10000)
			});

			// Occasionally generate trades
			if (Math.random() < 0.1) {
				const side = Math.random() > 0.5 ? 'BUY' : 'SELL';
				const tradePrice = side === 'BUY' ? ask : bid;
				const qty = Math.floor(5 + Math.random() * 15);

				tradeId++;
				addTrade({
					id: `trade-${tradeId}`,
					timestamp: new Date().toISOString(),
					symbol: 'AAPL',
					side: side as 'BUY' | 'SELL',
					price: tradePrice,
					quantity: qty
				});

				// Update position
				position += side === 'BUY' ? qty : -qty;
				totalPnl += (Math.random() - 0.4) * 10; // Slight positive bias

				updatePosition({
					symbol: 'AAPL',
					quantity: position,
					avg_cost: price,
					unrealized_pnl: position * (Math.random() - 0.5) * 0.5,
					realized_pnl: totalPnl
				});

				updateStrategyStatus({
					total_trades: tradeId,
					position,
					daily_pnl: totalPnl,
					current_bid: bid - 0.02,
					current_ask: ask + 0.02,
					active_orders: 2
				});
			}
		}, 200);
	}

	$: lastPrice = $currentTick?.last || ($currentTick?.bid && $currentTick?.ask ? ($currentTick.bid + $currentTick.ask) / 2 : 0);
	$: priceChange = lastPrice > 0 ? ((lastPrice - 150) / 150) * 100 : 0;
</script>

<div class="dashboard">
	<header class="header">
		<h1>
			<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
				<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline>
			</svg>
			TradeTool Dashboard
		</h1>
		<div class="status">
			<div class="status-indicator">
				<span class="status-dot" class:connected={$connected || demoMode}></span>
				<span>{$connected ? 'Live' : demoMode ? 'Demo' : 'Disconnected'}</span>
			</div>
			<div class="symbol-badge">
				AAPL
			</div>
		</div>
	</header>

	<div class="metrics-grid">
		<MetricCard
			label="Last Price"
			value={lastPrice}
			format="currency"
			change={priceChange}
			positive={priceChange >= 0}
		/>
		<MetricCard
			label="Spread"
			value={$orderBook.spread_bps}
			format="number"
			positive={null}
		/>
		<MetricCard
			label="Total P&L"
			value={$metrics.total_pnl}
			format="currency"
			positive={$metrics.total_pnl >= 0}
		/>
		<MetricCard
			label="Total Trades"
			value={$metrics.total_trades}
			format="number"
			positive={null}
		/>
	</div>

	<div class="main-content">
		<div class="panel chart-container">
			<div class="panel-header">Price Chart</div>
			<div class="panel-content" style="height: calc(100% - 45px); padding: 0;">
				<PriceChart />
			</div>
		</div>
	</div>

	<div class="sidebar">
		<div class="panel">
			<div class="panel-header">Order Book</div>
			<div class="panel-content">
				<OrderBook />
			</div>
		</div>

		<div class="panel">
			<div class="panel-header">Position</div>
			<div class="panel-content">
				<PositionPanel />
			</div>
		</div>

		<div class="panel">
			<div class="panel-header">Strategy</div>
			<div class="panel-content">
				<StrategyControls />
			</div>
		</div>

		<div class="panel">
			<div class="panel-header">Recent Trades</div>
			<div class="panel-content">
				<TradeHistory />
			</div>
		</div>
	</div>
</div>

<style>
	.symbol-badge {
		background: var(--bg-tertiary);
		padding: 4px 12px;
		border-radius: 4px;
		font-weight: 600;
		font-size: 0.875rem;
	}
</style>
