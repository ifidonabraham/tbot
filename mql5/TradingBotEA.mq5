#property copyright "TradingBot"
#property version   "2.00"
#property strict

#include <Trade/Trade.mqh>

CTrade trade;

input string InpWatchlist = "EURUSD,GBPUSD,USDJPY,USDCHF,USDCAD,AUDUSD,NZDUSD,EURGBP,EURJPY,EURCAD,EURAUD,GBPCHF";
input ENUM_TIMEFRAMES InpEntryTimeframe = PERIOD_M1;
input ENUM_TIMEFRAMES InpTrendTimeframe = PERIOD_M5;
input double InpVolume = 0.01;
input double InpMaxVolume = 0.01;
input int InpMagic = 260514;
input int InpDeviationPoints = 30;
input bool InpAllowNewEntries = true;
input bool InpResetMemoryOnStart = true;

input double InpMinimumUsableScore = 35.0;
input double InpMinimumDirectionalEdge = 18.0;
input int InpMaxNewPositionsPerScan = 10;
input int InpMaxOpenPositions = 12;
input int InpMaxPositionsPerSymbol = 1;
input int InpMaxCurrencyExposure = 2;
input int InpEntryScanSeconds = 2;

input double InpMaxSpreadPercent = 0.012;
input double InpMaxCandleRangePercent = 1.2;
input double InpMinVolumeRatio = 0.50;

input double InpMicroProfitMinMoney = 0.03;
input double InpMicroProfitGivebackMoney = 0.01;
input double InpTightGivebackMoney = 0.01;
input double InpMaxLossMoneyPerPosition = 0.15;
input double InpMaxBrokerStopLossMoney = 0.25;
input int InpMaxHoldSeconds = 90;
input int InpRollingCheckSeconds = 30;
input int InpRollingLookbackChecks = 10;

input double InpMaxDailyLossMoney = 25.0;
input double InpMaxDailyLossPercent = 20.0;

string Symbols[];
datetime LastEntryScan = 0;
datetime LastRollingCheck = 0;
datetime CurrentDay = 0;
double DailyRealizedPnl = 0.0;
double OpenProfitHistory[];

struct Setup
{
   string symbol;
   ENUM_ORDER_TYPE side;
   double score;
   double volume;
};

string Trim(string value)
{
   StringTrimLeft(value);
   StringTrimRight(value);
   return value;
}

double Clamp(double value, double low = 0.0, double high = 100.0)
{
   return MathMax(low, MathMin(high, value));
}

int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpDeviationPoints);
   if(InpResetMemoryOnStart)
      ClearAllBotGlobals();

   string raw[];
   int count = StringSplit(InpWatchlist, ',', raw);
   ArrayResize(Symbols, 0);
   for(int i = 0; i < count; i++)
   {
      string symbol = Trim(raw[i]);
      if(symbol == "")
         continue;
      SymbolSelect(symbol, true);
      int size = ArraySize(Symbols);
      ArrayResize(Symbols, size + 1);
      Symbols[size] = symbol;
   }

   ResetDailyIfNeeded();
   EventSetTimer(1);
   Print("TradingBot v2 micro demo started. Symbols: ", ArraySize(Symbols));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTick()
{
   ResetDailyIfNeeded();
   ManagePositions();
   MaybeScanEntries();
}

void OnTimer()
{
   ResetDailyIfNeeded();
   MaybeScanEntries();
}

void MaybeScanEntries()
{
   datetime now = TimeCurrent();
   if(now - LastEntryScan < InpEntryScanSeconds)
      return;
   LastEntryScan = now;
   ScanAndTrade();
}

void ResetDailyIfNeeded()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   dt.hour = 0;
   dt.min = 0;
   dt.sec = 0;
   datetime today = StructToTime(dt);
   if(CurrentDay != today)
   {
      CurrentDay = today;
      DailyRealizedPnl = 0.0;
      ArrayResize(OpenProfitHistory, 0);
   }
}

double DailyLossLimit()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double percentLimit = balance * InpMaxDailyLossPercent / 100.0;
   if(InpMaxDailyLossMoney > 0.0)
      return MathMin(percentLimit, InpMaxDailyLossMoney);
   return percentLimit;
}

bool DailyLossAllowsEntries()
{
   double limit = DailyLossLimit();
   return limit <= 0.0 || DailyRealizedPnl > -limit;
}

void ClearAllBotGlobals()
{
   for(int i = GlobalVariablesTotal() - 1; i >= 0; i--)
   {
      string name = GlobalVariableName(i);
      if(StringFind(name, "TradingBotV2") == 0 || StringFind(name, "TradingBot") == 0)
         GlobalVariableDel(name);
   }
   Print("TradingBot memory reset complete.");
}

string PeakMoneyKey(ulong ticket)
{
   return "TradingBotV2PeakMoney_" + IntegerToString((long)ticket);
}

double GetPeakMoney(ulong ticket, double currentProfit)
{
   string key = PeakMoneyKey(ticket);
   if(!GlobalVariableCheck(key))
      GlobalVariableSet(key, currentProfit);
   double peak = GlobalVariableGet(key);
   if(currentProfit > peak)
   {
      peak = currentProfit;
      GlobalVariableSet(key, peak);
   }
   return peak;
}

void ClearPositionState(ulong ticket)
{
   string key = PeakMoneyKey(ticket);
   if(GlobalVariableCheck(key))
      GlobalVariableDel(key);
}

void ManagePositions()
{
   UpdateOpenProfitHistory();
   bool tight = RollingProfitWeakening();
   double giveback = tight ? InpTightGivebackMoney : InpMicroProfitGivebackMoney;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;

      string symbol = PositionGetString(POSITION_SYMBOL);
      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      EnsureBrokerStop(symbol, ticket, type);

      double profit = PositionGetDouble(POSITION_PROFIT);
      double peak = GetPeakMoney(ticket, profit);
      datetime openTime = (datetime)PositionGetInteger(POSITION_TIME);
      string reason = "";

      if(profit <= -InpMaxLossMoneyPerPosition)
         reason = "MONEY_STOP";
      else if(InpMaxHoldSeconds > 0 && TimeCurrent() - openTime >= InpMaxHoldSeconds && peak < InpMicroProfitMinMoney)
         reason = "MAX_HOLD_NO_PROFIT";
      else if(peak >= InpMicroProfitMinMoney && profit <= peak - giveback)
         reason = tight ? "TIGHT_PEAK_GIVEBACK" : "PEAK_GIVEBACK";
      else if(peak >= InpMicroProfitMinMoney && profit <= 0.0)
         reason = "PROFIT_ERASED";

      if(reason != "")
         ClosePosition(ticket, reason);
   }
}

bool ClosePosition(ulong ticket, string reason)
{
   if(ticket == 0 || !PositionSelectByTicket(ticket))
      return false;
   string symbol = PositionGetString(POSITION_SYMBOL);
   double before = PositionGetDouble(POSITION_PROFIT);
   if(trade.PositionClose(ticket))
   {
      DailyRealizedPnl += before;
      ClearPositionState(ticket);
      Print("Closed ", symbol, " ticket=", ticket, " reason=", reason, " pnl=", DoubleToString(before, 2));
      return true;
   }
   Print("Close failed ", symbol, " ticket=", ticket, " reason=", reason, " error=", GetLastError());
   return false;
}

double TotalOpenBotProfit()
{
   double total = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      total += PositionGetDouble(POSITION_PROFIT);
   }
   return total;
}

void UpdateOpenProfitHistory()
{
   datetime now = TimeCurrent();
   if(now - LastRollingCheck < InpRollingCheckSeconds)
      return;
   LastRollingCheck = now;

   int size = ArraySize(OpenProfitHistory);
   ArrayResize(OpenProfitHistory, size + 1);
   OpenProfitHistory[size] = TotalOpenBotProfit();

   int maxSize = MathMax(2, InpRollingLookbackChecks);
   if(ArraySize(OpenProfitHistory) > maxSize)
   {
      for(int i = 1; i < ArraySize(OpenProfitHistory); i++)
         OpenProfitHistory[i - 1] = OpenProfitHistory[i];
      ArrayResize(OpenProfitHistory, maxSize);
   }
}

bool RollingProfitWeakening()
{
   int size = ArraySize(OpenProfitHistory);
   if(size < 2)
      return false;
   return OpenProfitHistory[size - 1] <= OpenProfitHistory[0];
}

void ScanAndTrade()
{
   if(!InpAllowNewEntries || !DailyLossAllowsEntries())
      return;

   Setup setups[];
   ArrayResize(setups, 0);
   for(int i = 0; i < ArraySize(Symbols); i++)
   {
      string symbol = Symbols[i];
      if(!SymbolSelect(symbol, true))
         continue;
      if(!EntryBaseAllowed(symbol))
         continue;

      double buyScore = 0.0;
      double sellScore = 0.0;
      bool buyOk = EntryScore(symbol, ORDER_TYPE_BUY, buyScore);
      bool sellOk = EntryScore(symbol, ORDER_TYPE_SELL, sellScore);

      double edge = MathAbs(buyScore - sellScore);
      Print("Scan ", symbol, " buy=", DoubleToString(buyScore, 2), " sell=", DoubleToString(sellScore, 2), " edge=", DoubleToString(edge, 2), " minScore=", DoubleToString(InpMinimumUsableScore, 2), " minEdge=", DoubleToString(InpMinimumDirectionalEdge, 2));

      if(buyOk && sellOk && buyScore >= InpMinimumUsableScore && buyScore - sellScore >= InpMinimumDirectionalEdge && EntrySideAllowed(symbol, ORDER_TYPE_BUY))
         AddSetup(setups, symbol, ORDER_TYPE_BUY, buyScore, TradeVolume(symbol));
      else if(buyOk && sellOk && sellScore >= InpMinimumUsableScore && sellScore - buyScore >= InpMinimumDirectionalEdge && EntrySideAllowed(symbol, ORDER_TYPE_SELL))
         AddSetup(setups, symbol, ORDER_TYPE_SELL, sellScore, TradeVolume(symbol));
   }

   SortSetupsByScore(setups);
   int opened = 0;
   for(int i = 0; i < ArraySize(setups) && opened < InpMaxNewPositionsPerScan; i++)
   {
      Setup best = setups[i];
      if(best.symbol == "" || best.volume <= 0.0)
         continue;
      if(!BrokerStopRiskAllowed(best.symbol, best.volume))
         continue;

      double sl = InitialStopLoss(best.symbol, best.side, best.volume);
      bool ok = best.side == ORDER_TYPE_BUY
         ? trade.Buy(best.volume, best.symbol, 0.0, sl, 0.0, "TradingBot BUY")
         : trade.Sell(best.volume, best.symbol, 0.0, sl, 0.0, "TradingBot SELL");
      if(ok)
      {
         opened++;
         Print("Opened ", EnumToString(best.side), " ", best.symbol, " score=", DoubleToString(best.score, 2), " volume=", DoubleToString(best.volume, 2));
      }
      else
         Print("Open failed ", best.symbol, " ", EnumToString(best.side), " error=", GetLastError());
   }
}

bool EntryBaseAllowed(string symbol)
{
   if(CountBotPositions("") >= InpMaxOpenPositions)
      return false;
   if(CountBotPositions(symbol) >= InpMaxPositionsPerSymbol)
      return false;
   if(CurrencyExposureCount(symbol) >= InpMaxCurrencyExposure)
      return false;
   if(SpreadPercent(symbol) > InpMaxSpreadPercent)
      return false;
   return true;
}

bool EntrySideAllowed(string symbol, ENUM_ORDER_TYPE side)
{
   return true;
}

int CountBotPositions(string symbol = "")
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      if(symbol != "" && PositionGetString(POSITION_SYMBOL) != symbol)
         continue;
      count++;
   }
   return count;
}

string BaseCurrency(string symbol)
{
   return StringSubstr(symbol, 0, 3);
}

string QuoteCurrency(string symbol)
{
   return StringSubstr(symbol, 3, 3);
}

int CurrencyExposureCount(string symbol)
{
   string base = BaseCurrency(symbol);
   string quote = QuoteCurrency(symbol);
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      string s = PositionGetString(POSITION_SYMBOL);
      string b = BaseCurrency(s);
      string q = QuoteCurrency(s);
      if(b == base || b == quote || q == base || q == quote)
         count++;
   }
   return count;
}

double TradeVolume(string symbol)
{
   double minVol = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxVol = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(minVol > InpMaxVolume)
   {
      Print("Entry blocked by broker minimum volume ", symbol, " min=", DoubleToString(minVol, 2), " maxAllowed=", DoubleToString(InpMaxVolume, 2));
      return 0.0;
   }

   double volume = MathMin(InpVolume, InpMaxVolume);
   volume = MathMax(volume, minVol);
   volume = MathMin(volume, maxVol);
   if(step > 0.0)
      volume = MathFloor(volume / step) * step;
   return NormalizeDouble(volume, 2);
}

double PriceDistanceForMoney(string symbol, double volume, double money)
{
   double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickValue <= 0.0 || tickSize <= 0.0 || volume <= 0.0 || money <= 0.0)
      return 0.0;
   double moneyDistance = money * tickSize / (tickValue * volume);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   int stopsLevel = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
   int freezeLevel = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   double brokerMinimum = MathMax(stopsLevel, freezeLevel) * point;
   if(brokerMinimum > 0.0)
      brokerMinimum += point * 2.0;
   return MathMax(moneyDistance, brokerMinimum);
}

double MoneyForPriceDistance(string symbol, double volume, double distance)
{
   double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickValue <= 0.0 || tickSize <= 0.0 || volume <= 0.0 || distance <= 0.0)
      return 0.0;
   return distance / tickSize * tickValue * volume;
}

bool BrokerStopRiskAllowed(string symbol, double volume)
{
   double distance = PriceDistanceForMoney(symbol, volume, InpMaxLossMoneyPerPosition);
   double moneyRisk = MoneyForPriceDistance(symbol, volume, distance);
   if(moneyRisk > InpMaxBrokerStopLossMoney)
   {
      Print("Entry blocked by broker stop distance ", symbol, " risk=", DoubleToString(moneyRisk, 2), " max=", DoubleToString(InpMaxBrokerStopLossMoney, 2));
      return false;
   }
   return true;
}

double InitialStopLoss(string symbol, ENUM_ORDER_TYPE side, double volume)
{
   double distance = PriceDistanceForMoney(symbol, volume, InpMaxLossMoneyPerPosition);
   if(distance <= 0.0)
      distance = SymbolInfoDouble(symbol, SYMBOL_POINT) * 100.0;

   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   if(side == ORDER_TYPE_BUY)
      return NormalizeDouble(SymbolInfoDouble(symbol, SYMBOL_ASK) - distance, digits);
   return NormalizeDouble(SymbolInfoDouble(symbol, SYMBOL_BID) + distance, digits);
}

void EnsureBrokerStop(string symbol, ulong ticket, ENUM_POSITION_TYPE type)
{
   if(!PositionSelectByTicket(ticket))
      return;
   if(PositionGetDouble(POSITION_SL) != 0.0)
      return;

   double volume = PositionGetDouble(POSITION_VOLUME);
   double distance = PriceDistanceForMoney(symbol, volume, InpMaxLossMoneyPerPosition);
   if(distance <= 0.0)
      distance = SymbolInfoDouble(symbol, SYMBOL_POINT) * 100.0;

   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   double open = PositionGetDouble(POSITION_PRICE_OPEN);
   double tp = PositionGetDouble(POSITION_TP);
   double sl = type == POSITION_TYPE_BUY
      ? NormalizeDouble(open - distance, digits)
      : NormalizeDouble(open + distance, digits);
   if(!trade.PositionModify(ticket, sl, tp))
      Print("Initial broker SL failed ", symbol, " ticket=", ticket, " error=", GetLastError());
}

double SpreadPercent(string symbol)
{
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double mid = (ask + bid) / 2.0;
   if(ask <= 0.0 || bid <= 0.0 || mid <= 0.0)
      return 100.0;
   return (ask - bid) / mid * 100.0;
}

bool EntryScore(string symbol, ENUM_ORDER_TYPE side, double &score)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(symbol, InpEntryTimeframe, 0, 60, rates) < 40)
      return false;

   double rangePercent = (rates[1].high - rates[1].low) / MathMax(rates[1].close, 0.00001) * 100.0;
   if(rangePercent > InpMaxCandleRangePercent)
      return false;

   double volumeRatio = VolumeRatio(rates);
   if(volumeRatio < InpMinVolumeRatio)
      return false;

   double rsiScore = side == ORDER_TYPE_BUY ? RsiBuyScore(symbol) : RsiSellScore(symbol);
   double bbScore = side == ORDER_TYPE_BUY ? BollingerBuyScore(symbol) : BollingerSellScore(symbol);
   double momentumScore = side == ORDER_TYPE_BUY ? MomentumBuyScore(symbol, rates) : MomentumSellScore(symbol, rates);
   double trendScore = TrendScore(symbol, side);
   double volumeScore = Clamp(35.0 + (volumeRatio - 1.0) * 45.0);

   score = rsiScore * 0.30 + bbScore * 0.30 + momentumScore * 0.20 + trendScore * 0.10 + volumeScore * 0.10;
   return true;
}

double VolumeRatio(const MqlRates &rates[])
{
   double sum = 0.0;
   for(int i = 2; i <= 21; i++)
      sum += (double)rates[i].tick_volume;
   double avg = sum / 20.0;
   if(avg <= 0.0)
      return 0.0;
   return (double)rates[1].tick_volume / avg;
}

double RsiBuyScore(string symbol)
{
   double rsi[];
   ArraySetAsSeries(rsi, true);
   int h = iRSI(symbol, InpEntryTimeframe, 14, PRICE_CLOSE);
   if(h == INVALID_HANDLE || CopyBuffer(h, 0, 0, 3, rsi) < 3)
      return 0.0;
   IndicatorRelease(h);
   double oversold = Clamp((45.0 - rsi[0]) * 3.0);
   double turn = rsi[0] > rsi[1] ? 30.0 : 0.0;
   return Clamp(oversold + turn);
}

double RsiSellScore(string symbol)
{
   double rsi[];
   ArraySetAsSeries(rsi, true);
   int h = iRSI(symbol, InpEntryTimeframe, 14, PRICE_CLOSE);
   if(h == INVALID_HANDLE || CopyBuffer(h, 0, 0, 3, rsi) < 3)
      return 0.0;
   IndicatorRelease(h);
   double overbought = Clamp((rsi[0] - 55.0) * 3.0);
   double turn = rsi[0] < rsi[1] ? 30.0 : 0.0;
   return Clamp(overbought + turn);
}

double BollingerBuyScore(string symbol)
{
   double middle[], upper[], lower[];
   ArraySetAsSeries(middle, true);
   ArraySetAsSeries(upper, true);
   ArraySetAsSeries(lower, true);
   int h = iBands(symbol, InpEntryTimeframe, 20, 0, 2.0, PRICE_CLOSE);
   if(h == INVALID_HANDLE || CopyBuffer(h, 0, 0, 2, middle) < 2 || CopyBuffer(h, 1, 0, 2, upper) < 2 || CopyBuffer(h, 2, 0, 2, lower) < 2)
      return 0.0;
   IndicatorRelease(h);
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double width = MathMax(middle[0] - lower[0], SymbolInfoDouble(symbol, SYMBOL_POINT));
   return Clamp((lower[0] - bid) / width * 80.0 + 50.0);
}

double BollingerSellScore(string symbol)
{
   double middle[], upper[], lower[];
   ArraySetAsSeries(middle, true);
   ArraySetAsSeries(upper, true);
   ArraySetAsSeries(lower, true);
   int h = iBands(symbol, InpEntryTimeframe, 20, 0, 2.0, PRICE_CLOSE);
   if(h == INVALID_HANDLE || CopyBuffer(h, 0, 0, 2, middle) < 2 || CopyBuffer(h, 1, 0, 2, upper) < 2 || CopyBuffer(h, 2, 0, 2, lower) < 2)
      return 0.0;
   IndicatorRelease(h);
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double width = MathMax(upper[0] - middle[0], SymbolInfoDouble(symbol, SYMBOL_POINT));
   return Clamp((ask - upper[0]) / width * 80.0 + 50.0);
}

double MomentumBuyScore(string symbol, const MqlRates &rates[])
{
   if(rates[0].close > rates[1].close && rates[1].close >= rates[2].close)
      return 100.0;
   if(rates[0].close > rates[1].close)
      return 65.0;
   return 25.0;
}

double MomentumSellScore(string symbol, const MqlRates &rates[])
{
   if(rates[0].close < rates[1].close && rates[1].close <= rates[2].close)
      return 100.0;
   if(rates[0].close < rates[1].close)
      return 65.0;
   return 25.0;
}

double TrendScore(string symbol, ENUM_ORDER_TYPE side)
{
   double ema9[], ema21[];
   ArraySetAsSeries(ema9, true);
   ArraySetAsSeries(ema21, true);
   int h9 = iMA(symbol, InpTrendTimeframe, 9, 0, MODE_EMA, PRICE_CLOSE);
   int h21 = iMA(symbol, InpTrendTimeframe, 21, 0, MODE_EMA, PRICE_CLOSE);
   if(h9 == INVALID_HANDLE || h21 == INVALID_HANDLE)
      return 50.0;
   if(CopyBuffer(h9, 0, 0, 2, ema9) < 2 || CopyBuffer(h21, 0, 0, 2, ema21) < 2)
   {
      IndicatorRelease(h9);
      IndicatorRelease(h21);
      return 50.0;
   }
   IndicatorRelease(h9);
   IndicatorRelease(h21);

   if(side == ORDER_TYPE_BUY)
      return ema9[0] >= ema21[0] ? 100.0 : 40.0;
   return ema9[0] <= ema21[0] ? 100.0 : 40.0;
}

void AddSetup(Setup &setups[], string symbol, ENUM_ORDER_TYPE side, double score, double volume)
{
   int size = ArraySize(setups);
   ArrayResize(setups, size + 1);
   setups[size].symbol = symbol;
   setups[size].side = side;
   setups[size].score = score;
   setups[size].volume = volume;
}

void SortSetupsByScore(Setup &setups[])
{
   int total = ArraySize(setups);
   for(int i = 0; i < total - 1; i++)
   {
      for(int j = i + 1; j < total; j++)
      {
         if(setups[j].score > setups[i].score)
         {
            Setup tmp = setups[i];
            setups[i] = setups[j];
            setups[j] = tmp;
         }
      }
   }
}
