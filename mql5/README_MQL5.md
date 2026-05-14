# TradingBot MT5 Expert Advisor

This folder contains the native MetaTrader 5 version of the bot:

- `TradingBotEA.mq5`

## How To Install

1. Open MetaTrader 5.
2. Click `File > Open Data Folder`.
3. Open `MQL5 > Experts`.
4. Copy `TradingBotEA.mq5` into that `Experts` folder.
5. Open MetaEditor.
6. Open `TradingBotEA.mq5`.
7. Click `Compile`.
8. Return to MT5.
9. In Navigator, refresh `Expert Advisors`.
10. Attach `TradingBotEA` to one chart.
11. Enable `Algo Trading`.

The EA scans the symbols in `InpWatchlist`, so you only need to attach it to one chart.

## Current Logic

- Scans every symbol in `InpWatchlist`.
- Scores both BUY and SELL setups.
- BUY setups are strengthened by bullish 5-minute trend.
- SELL setups are strengthened by bearish 5-minute trend.
- Requires confirmation candle.
- Requires volume to meet the 20-candle average.
- Opens the best setup above `InpEntryThreshold`.
- Manages open positions every `InpPositionCheckSeconds`.
- Moves stop-loss to breakeven after profit reaches `InpBreakevenTriggerPercent`.
- Closes small profitable trades when momentum fades.
- Uses trailing giveback after profit has moved far enough.
- Pauses new entries after the daily loss limit.

## Important

This EA can still lose money. It is faster and more native to MT5 than Python, but it does not remove market risk, spread, slippage, or broker execution delay.
