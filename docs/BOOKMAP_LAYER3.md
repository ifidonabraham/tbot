# Layer 3 Order-Flow Confirmation

Layer 3 now works without Bookmap or TradingView as core dependencies.

The default confirmation source is MT5 tick data. Bookmap remains optional, but it is not required for the funnel to run.

The broad market scan should stay automated:

```text
MT5/Dukascopy/Massive/Alpha data
-> Layer 1 trend filter
-> Layer 2 support/resistance from historical candles
-> Layer 3 breakout + MT5 tick-flow confirmation
-> scalper shortlist
```

TradingView webhook levels and Bookmap order-flow can still be used later as enrichment, but they should not be required for scanning hundreds or thousands of instruments.

## Default MT5 Tick-Flow Alternative

Layer 3 checks recent MT5 tick movement after a breakout:

- BUY breakout: more upward ticks than downward ticks, price moving up, spread not too wide.
- SELL breakout: more downward ticks than upward ticks, price moving down, spread not too wide.

Default settings:

```env
LAYER2_USE_TRADINGVIEW_WEBHOOK=false
LAYER3_USE_BOOKMAP=false
LAYER3_USE_MT5_TICK_CONFIRMATION=true
LAYER3_TICK_LOOKBACK_SECONDS=60
LAYER3_MAX_TICKS=2000
LAYER3_MIN_TICKS=20
LAYER3_MIN_TICK_IMBALANCE=1.10
LAYER3_MAX_SPREAD_PERCENT=0.05
```

This is the correct default for your project because it avoids manual TradingView alerts and avoids requiring Bookmap subscriptions or manually loaded instruments.

## Optional Bookmap Mode

The Bookmap Python API from GitHub is not a normal HTTP API. It is an addon API that runs inside Bookmap and receives live depth/trade events. The bridge file in this project converts those events into a small JSON file that `market_funnel.py` can read.

## Files

- `bookmap_orderflow_bridge.py` runs inside the Bookmap Python API environment.
- `data/bookmap_orderflow.json` is the live signal file written by the bridge.
- `market_funnel.py` reads that JSON file when `LAYER3_USE_BOOKMAP=true`.

## Settings

Keep Bookmap disabled until the bridge is running:

```env
LAYER3_USE_BOOKMAP=false
BOOKMAP_ORDERFLOW_PATH=data/bookmap_orderflow.json
BOOKMAP_MAX_SIGNAL_AGE_SECONDS=5
BOOKMAP_MIN_IMBALANCE_RATIO=1.20
BOOKMAP_MIN_TRADE_DELTA=0
BOOKMAP_DEPTH_LEVELS=10
```

After Bookmap is running and `data/bookmap_orderflow.json` is updating, enable it:

```env
LAYER3_USE_BOOKMAP=true
```

## Signal Contract

The JSON file must look like this:

```json
{
  "EURUSD": {
    "timestamp": 1778840000.0,
    "bid_depth": 1200000.0,
    "ask_depth": 850000.0,
    "trade_delta": 42000.0,
    "last_price": 1.08765
  }
}
```

For a BUY breakout, Layer 3 requires:

- `bid_depth / ask_depth >= BOOKMAP_MIN_IMBALANCE_RATIO`
- `trade_delta >= BOOKMAP_MIN_TRADE_DELTA`
- signal age is below `BOOKMAP_MAX_SIGNAL_AGE_SECONDS`

For a SELL breakout, Layer 3 requires:

- `ask_depth / bid_depth >= BOOKMAP_MIN_IMBALANCE_RATIO`
- `trade_delta <= -BOOKMAP_MIN_TRADE_DELTA`
- signal age is below `BOOKMAP_MAX_SIGNAL_AGE_SECONDS`

## Optional Bookmap Run Order

1. Run the normal market funnel first.
2. Let Layers 1, 2, and 3 reduce the market to a small shortlist.
3. Only attach Bookmap to the shortlisted symbols if Bookmap is available.
3. Run `bookmap_orderflow_bridge.py` from the Bookmap Python API environment.
4. Confirm `data/bookmap_orderflow.json` is updating.
5. Set `LAYER3_USE_BOOKMAP=true`.
6. Run `python market_funnel.py`.

Do not enable Bookmap confirmation before the JSON file is updating, because Layer 3 will correctly block breakouts with stale or missing order-flow data.
