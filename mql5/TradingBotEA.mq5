#property copyright "TradingBot"
#property version   "2.00"
#property strict

#include <Trade/Trade.mqh>

CTrade trade;

const int BOT_MAGIC = 260514;
const double BOT_MINIMUM_USABLE_SCORE = 45.0;
const double BOT_MINIMUM_DIRECTIONAL_EDGE = 20.0;
const int BOT_MAX_NEW_POSITIONS_PER_SCAN = 1;
const int BOT_MAX_OPEN_POSITIONS = 1;
const int BOT_MAX_POSITIONS_PER_SYMBOL = 1;
const int BOT_MAX_CURRENCY_EXPOSURE = 1;
const double BOT_MAX_SPREAD_PERCENT = 0.045;
const double BOT_MAX_LOSS_MONEY_PER_POSITION = 0.08;
const double BOT_SESSION_LOSS_STOP = 0.08;
const bool BOT_PAUSE_WHEN_SESSION_NEGATIVE = true;

input string InpWatchlist = "EURUSD,GBPUSD,USDJPY,USDCAD,AUDUSD,EURGBP";
input bool InpUseMarketWatchSymbols = false;
input int InpMaxSymbolsToLoad = 200;
input ENUM_TIMEFRAMES InpEntryTimeframe = PERIOD_M1;
input ENUM_TIMEFRAMES InpTrendTimeframe = PERIOD_M5;
input double InpVolume = 0.01;
input double InpMaxVolume = 0.01;
input int InpDeviationPoints = 30;
input bool InpAllowNewEntries = true;
input bool InpResetMemoryOnStart = true;
input bool InpSingleInstance = true;
input bool InpResetDailyPnlOnStart = true;

input double InpMinimumUsableScore = 75.0;
input double InpMinimumDirectionalEdge = 30.0;
input int InpMaxNewPositionsPerScan = 1;
input int InpMaxOpenPositions = 2;
input int InpMaxPositionsPerSymbol = 1;
input int InpMaxCurrencyExposure = 1;
input int InpEntryScanSeconds = 2;
input int InpStartupWarmupSeconds = 30;

input double InpMaxSpreadPercent = 0.08;
input double InpMaxCandleRangePercent = 1.2;
input double InpMinVolumeRatio = 0.50;

input double InpMicroProfitMinMoney = 0.03;
input double InpMicroProfitGivebackMoney = 0.01;
input double InpPositiveProtectionTriggerMoney = 0.01;
input double InpMaxLossMoneyPerPosition = 0.08;
input double InpMaxBrokerStopLossMoney = 0.25;
input int InpMaxHoldSeconds = 180;
input int InpMinimumProfitHoldSeconds = 30;

input double InpMaxDailyLossMoney = 5.0;
input double InpMaxDailyLossPercent = 20.0;

input bool InpEnableLayer1TrendFunnel = true;
input int InpLayer1AdxPeriod = 14;
input double InpLayer1MinAdx = 25.0;
input int InpLayer1StructureBars = 3;

string Symbols[];
datetime LastEntryScan = 0;
datetime CurrentDay = 0;
double DailyRealizedPnl = 0.0;
datetime LastDailyLossLog = 0;
datetime BotStartedLocal = 0;
datetime BotStartedServer = 0;

struct Setup
{
   string symbol;
   ENUM_ORDER_TYPE side;
   double score;
   double volume;
};

enum Layer1Trend
{
   LAYER1_RANGING = 0,
   LAYER1_TRENDING_UP = 1,
   LAYER1_TRENDING_DOWN = -1
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
   trade.SetExpertMagicNumber(BOT_MAGIC);
   trade.SetDeviationInPoints(InpDeviationPoints);
   BotStartedLocal = TimeLocal();
   BotStartedServer = TimeCurrent();
   if(InpSingleInstance && !AcquireInstanceLock())
      return INIT_FAILED;
   if(InpResetMemoryOnStart)
      ClearAllBotGlobals();

   ArrayResize(Symbols, 0);
   if(InpUseMarketWatchSymbols)
   {
      int total = SymbolsTotal(true);
      for(int i = 0; i < total && ArraySize(Symbols) < InpMaxSymbolsToLoad; i++)
      {
         string symbol = SymbolName(i, true);
         if(symbol == "")
            continue;
         int size = ArraySize(Symbols);
         ArrayResize(Symbols, size + 1);
         Symbols[size] = symbol;
      }
   }
   else
   {
      string raw[];
      int count = StringSplit(InpWatchlist, ',', raw);
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
   }

   ResetDailyIfNeeded();
   if(InpResetDailyPnlOnStart)
      LoadSessionPnl();
   else
      LoadDailyRealizedPnlFromHistory();
   EventSetTimer(1);
   Print("TradingBot v2 micro demo started. Symbols: ", ArraySize(Symbols), " dailyPnl=", DoubleToString(DailyRealizedPnl, 2));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   ReleaseInstanceLock();
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
   TimeToStruct(TimeLocal(), dt);
   dt.hour = 0;
   dt.min = 0;
   dt.sec = 0;
   datetime today = StructToTime(dt);
   if(CurrentDay != today)
   {
      CurrentDay = today;
      if(InpResetDailyPnlOnStart)
         LoadSessionPnl();
      else
      {
         DailyRealizedPnl = 0.0;
         LoadDailyRealizedPnlFromHistory();
      }
   }
}

string SessionPnlKey()
{
   MqlDateTime dt;
   TimeToStruct(TimeLocal(), dt);
   return "TradingBotV2SessionPnl_" + IntegerToString(BOT_MAGIC) + "_" + StringFormat("%04d%02d%02d", dt.year, dt.mon, dt.day);
}

void LoadSessionPnl()
{
   string key = SessionPnlKey();
   if(GlobalVariableCheck(key))
      DailyRealizedPnl = GlobalVariableGet(key);
   else
   {
      DailyRealizedPnl = 0.0;
      if(CurrentDay > 0 && HistorySelect(CurrentDay, TimeCurrent() + 60))
      {
         int total = HistoryDealsTotal();
         for(int i = 0; i < total; i++)
         {
            ulong deal = HistoryDealGetTicket(i);
            if(deal == 0)
               continue;
            if((int)HistoryDealGetInteger(deal, DEAL_MAGIC) != BOT_MAGIC)
               continue;
            ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal, DEAL_ENTRY);
            if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_INOUT && entry != DEAL_ENTRY_OUT_BY)
               continue;
            DailyRealizedPnl += HistoryDealGetDouble(deal, DEAL_PROFIT);
            DailyRealizedPnl += HistoryDealGetDouble(deal, DEAL_COMMISSION);
            DailyRealizedPnl += HistoryDealGetDouble(deal, DEAL_SWAP);
         }
      }
      GlobalVariableSet(key, DailyRealizedPnl);
   }
}

void SaveSessionPnl()
{
   GlobalVariableSet(SessionPnlKey(), DailyRealizedPnl);
}

void LoadDailyRealizedPnlFromHistory()
{
   if(CurrentDay <= 0)
      return;
   double realized = 0.0;
   datetime startTime = InpResetDailyPnlOnStart ? BotStartedServer : CurrentDay;
   datetime endTime = TimeCurrent() + 60;
   if(!HistorySelect(startTime, endTime))
   {
      Print("Daily PnL history load failed error=", GetLastError());
      return;
   }
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0)
         continue;
      if((int)HistoryDealGetInteger(deal, DEAL_MAGIC) != BOT_MAGIC)
         continue;
      ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal, DEAL_ENTRY);
      if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_INOUT && entry != DEAL_ENTRY_OUT_BY)
         continue;
      realized += HistoryDealGetDouble(deal, DEAL_PROFIT);
      realized += HistoryDealGetDouble(deal, DEAL_COMMISSION);
      realized += HistoryDealGetDouble(deal, DEAL_SWAP);
   }
   DailyRealizedPnl = realized;
}

double DailyLossLimit()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double percentLimit = balance * InpMaxDailyLossPercent / 100.0;
   double configuredLimit = percentLimit;
   if(InpMaxDailyLossMoney > 0.0)
      configuredLimit = MathMin(percentLimit, InpMaxDailyLossMoney);
   return MathMin(MathMin(configuredLimit, 5.0), BOT_SESSION_LOSS_STOP);
}

bool DailyLossAllowsEntries()
{
   if(!TerminalInfoInteger(TERMINAL_CONNECTED))
      return false;

   if(TimeLocal() - BotStartedLocal < InpStartupWarmupSeconds)
   {
      if(TimeCurrent() - LastDailyLossLog >= 10)
      {
         LastDailyLossLog = TimeCurrent();
         Print("Entries paused: startup warmup active");
      }
      return false;
   }

   if(InpResetDailyPnlOnStart)
      LoadSessionPnl();
   else
      LoadDailyRealizedPnlFromHistory();
   double limit = DailyLossLimit();
   if(BOT_PAUSE_WHEN_SESSION_NEGATIVE && DailyRealizedPnl < 0.0)
   {
      if(TimeCurrent() - LastDailyLossLog >= 30)
      {
         LastDailyLossLog = TimeCurrent();
         Print("Entries paused: bot session is negative pnl=", DoubleToString(DailyRealizedPnl, 2));
      }
      return false;
   }

   bool allowed = limit <= 0.0 || DailyRealizedPnl > -limit;
   if(!allowed && TimeCurrent() - LastDailyLossLog >= 30)
   {
      LastDailyLossLog = TimeCurrent();
      Print("Entries paused: daily loss limit reached pnl=", DoubleToString(DailyRealizedPnl, 2), " limit=", DoubleToString(limit, 2));
   }
   return allowed;
}

void ClearAllBotGlobals()
{
   for(int i = GlobalVariablesTotal() - 1; i >= 0; i--)
   {
      string name = GlobalVariableName(i);
      if(name == InstanceLockKey() || StringFind(name, "TradingBotV2SessionPnl_") == 0)
         continue;
      if(StringFind(name, "TradingBotV2") == 0 || StringFind(name, "TradingBot") == 0)
         GlobalVariableDel(name);
   }
   Print("TradingBot memory reset complete.");
}

string InstanceLockKey()
{
   return "TradingBotV2InstanceLock_" + IntegerToString(BOT_MAGIC);
}

bool AcquireInstanceLock()
{
   string key = InstanceLockKey();
   double thisChart = (double)ChartID();
   if(GlobalVariableCheck(key))
   {
      double owner = GlobalVariableGet(key);
      if(owner != thisChart)
      {
         Print("TradingBot blocked: another EA instance is already active. Remove the duplicate EA from other charts first. ownerChart=", DoubleToString(owner, 0), " thisChart=", DoubleToString(thisChart, 0));
         return false;
      }
   }
   GlobalVariableSet(key, thisChart);
   return true;
}

void ReleaseInstanceLock()
{
   if(!InpSingleInstance)
      return;
   string key = InstanceLockKey();
   if(!GlobalVariableCheck(key))
      return;
   double owner = GlobalVariableGet(key);
   if(owner == (double)ChartID())
      GlobalVariableDel(key);
}

string PeakMoneyKey(ulong ticket)
{
   return "TradingBotV2PeakMoney_" + IntegerToString((long)ticket);
}

double GetPeakMoney(ulong ticket, double currentProfit)
{
   string key = PeakMoneyKey(ticket);
   if(!GlobalVariableCheck(key))
      GlobalVariableSet(key, 0.0);
   double peak = GlobalVariableGet(key);
   if(currentProfit > 0.0 && currentProfit > peak)
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
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != BOT_MAGIC)
         continue;

      string symbol = PositionGetString(POSITION_SYMBOL);
      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      EnsureBrokerStop(symbol, ticket, type);

      double profit = PositionGetDouble(POSITION_PROFIT);
      double peak = GetPeakMoney(ticket, profit);
      datetime openTime = (datetime)PositionGetInteger(POSITION_TIME);
      int ageSeconds = (int)(TimeCurrent() - openTime);
      bool profitExitAllowed = ageSeconds >= InpMinimumProfitHoldSeconds;
      string reason = "";

      if(profit <= -BOT_MAX_LOSS_MONEY_PER_POSITION)
         reason = "MONEY_STOP";
      else if(profitExitAllowed && peak >= InpPositiveProtectionTriggerMoney && profit <= 0.0)
         reason = "PROFIT_TURNED_NEGATIVE";
      else if(InpMaxHoldSeconds > 0 && ageSeconds >= InpMaxHoldSeconds)
         reason = "MAX_HOLD_TIMEOUT";
      else if(profitExitAllowed && peak >= InpMicroProfitMinMoney && profit <= peak - InpMicroProfitGivebackMoney)
         reason = "PEAK_GIVEBACK";
      else if(profitExitAllowed && peak >= InpMicroProfitMinMoney && profit <= 0.0)
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
      SaveSessionPnl();
      ClearPositionState(ticket);
      Print("Closed ", symbol, " ticket=", ticket, " reason=", reason, " pnl=", DoubleToString(before, 2));
      return true;
   }
   Print("Close failed ", symbol, " ticket=", ticket, " reason=", reason, " error=", GetLastError());
   return false;
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

      string layer1Reason = "";
      Layer1Trend layer1 = LAYER1_RANGING;
      if(InpEnableLayer1TrendFunnel)
         layer1 = Layer1TrendState(symbol, layer1Reason);
      if(InpEnableLayer1TrendFunnel && layer1 == LAYER1_RANGING)
      {
         Print("Layer1 eliminated ", symbol, " state=RANGING reason=", layer1Reason);
         continue;
      }

      double buyScore = 0.0;
      double sellScore = 0.0;
      bool buyOk = EntryScore(symbol, ORDER_TYPE_BUY, buyScore);
      bool sellOk = EntryScore(symbol, ORDER_TYPE_SELL, sellScore);

      double edge = MathAbs(buyScore - sellScore);
      Print("Scan ", symbol, " layer1=", Layer1TrendName(layer1), " buy=", DoubleToString(buyScore, 2), " sell=", DoubleToString(sellScore, 2), " edge=", DoubleToString(edge, 2), " minScore=", DoubleToString(BOT_MINIMUM_USABLE_SCORE, 2), " minEdge=", DoubleToString(BOT_MINIMUM_DIRECTIONAL_EDGE, 2));

      if((!InpEnableLayer1TrendFunnel || layer1 == LAYER1_TRENDING_UP) && buyOk && sellOk && buyScore >= BOT_MINIMUM_USABLE_SCORE && buyScore - sellScore >= BOT_MINIMUM_DIRECTIONAL_EDGE && EntrySideAllowed(symbol, ORDER_TYPE_BUY))
         AddSetup(setups, symbol, ORDER_TYPE_BUY, buyScore, TradeVolume(symbol));
      else if((!InpEnableLayer1TrendFunnel || layer1 == LAYER1_TRENDING_DOWN) && buyOk && sellOk && sellScore >= BOT_MINIMUM_USABLE_SCORE && sellScore - buyScore >= BOT_MINIMUM_DIRECTIONAL_EDGE && EntrySideAllowed(symbol, ORDER_TYPE_SELL))
         AddSetup(setups, symbol, ORDER_TYPE_SELL, sellScore, TradeVolume(symbol));
   }

   SortSetupsByScore(setups);
   int opened = 0;
   for(int i = 0; i < ArraySize(setups) && opened < BOT_MAX_NEW_POSITIONS_PER_SCAN; i++)
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
   if(CountBotPositions("") >= BOT_MAX_OPEN_POSITIONS)
   {
      Print("Entry blocked ", symbol, " reason=MAX_OPEN_POSITIONS");
      return false;
   }
   if(CountBotPositions(symbol) >= BOT_MAX_POSITIONS_PER_SYMBOL)
   {
      Print("Entry blocked ", symbol, " reason=MAX_SYMBOL_POSITIONS");
      return false;
   }
   if(CurrencyExposureCount(symbol) >= BOT_MAX_CURRENCY_EXPOSURE)
   {
      Print("Entry blocked ", symbol, " reason=CURRENCY_EXPOSURE");
      return false;
   }
   double spread = SpreadPercent(symbol);
   if(spread > BOT_MAX_SPREAD_PERCENT)
   {
      Print("Entry blocked ", symbol, " reason=SPREAD spread=", DoubleToString(spread, 5), " max=", DoubleToString(BOT_MAX_SPREAD_PERCENT, 5));
      return false;
   }
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
      if((int)PositionGetInteger(POSITION_MAGIC) != BOT_MAGIC)
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
      if((int)PositionGetInteger(POSITION_MAGIC) != BOT_MAGIC)
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
   double distance = PriceDistanceForMoney(symbol, volume, BOT_MAX_LOSS_MONEY_PER_POSITION);
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
   double distance = PriceDistanceForMoney(symbol, volume, BOT_MAX_LOSS_MONEY_PER_POSITION);
   if(distance <= 0.0)
      distance = SymbolInfoDouble(symbol, SYMBOL_POINT) * 100.0;

   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   if(side == ORDER_TYPE_BUY)
      return NormalizeDouble(bid - distance, digits);
   return NormalizeDouble(ask + distance, digits);
}

void EnsureBrokerStop(string symbol, ulong ticket, ENUM_POSITION_TYPE type)
{
   if(!PositionSelectByTicket(ticket))
      return;
   if(PositionGetDouble(POSITION_SL) != 0.0)
      return;

   double volume = PositionGetDouble(POSITION_VOLUME);
   double distance = PriceDistanceForMoney(symbol, volume, BOT_MAX_LOSS_MONEY_PER_POSITION);
   if(distance <= 0.0)
      distance = SymbolInfoDouble(symbol, SYMBOL_POINT) * 100.0;

   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   double tp = PositionGetDouble(POSITION_TP);
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double sl = type == POSITION_TYPE_BUY
      ? NormalizeDouble(bid - distance, digits)
      : NormalizeDouble(ask + distance, digits);
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

string Layer1TrendName(Layer1Trend trend)
{
   if(trend == LAYER1_TRENDING_UP)
      return "TRENDING_UP";
   if(trend == LAYER1_TRENDING_DOWN)
      return "TRENDING_DOWN";
   return "RANGING";
}

Layer1Trend Layer1TrendState(string symbol, string &reason)
{
   string h1Reason = "";
   string h4Reason = "";
   Layer1Trend h1 = TimeframeTrendState(symbol, PERIOD_H1, h1Reason);
   Layer1Trend h4 = TimeframeTrendState(symbol, PERIOD_H4, h4Reason);

   if(h1 == LAYER1_TRENDING_UP && h4 == LAYER1_TRENDING_UP)
   {
      reason = "H1/H4 bullish agreement";
      return LAYER1_TRENDING_UP;
   }
   if(h1 == LAYER1_TRENDING_DOWN && h4 == LAYER1_TRENDING_DOWN)
   {
      reason = "H1/H4 bearish agreement";
      return LAYER1_TRENDING_DOWN;
   }

   reason = "H1=" + Layer1TrendName(h1) + " (" + h1Reason + "), H4=" + Layer1TrendName(h4) + " (" + h4Reason + ")";
   return LAYER1_RANGING;
}

Layer1Trend TimeframeTrendState(string symbol, ENUM_TIMEFRAMES timeframe, string &reason)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int barsNeeded = MathMax(InpLayer1StructureBars + 2, 60);
   if(CopyRates(symbol, timeframe, 0, barsNeeded, rates) < barsNeeded)
   {
      reason = "not enough OHLCV";
      return LAYER1_RANGING;
   }

   double ema9 = 0.0;
   double ema21 = 0.0;
   double ema50 = 0.0;
   double ema200 = 0.0;
   if(!EmaValue(symbol, timeframe, 9, ema9) || !EmaValue(symbol, timeframe, 21, ema21) || !EmaValue(symbol, timeframe, 50, ema50) || !EmaValue(symbol, timeframe, 200, ema200))
   {
      reason = "EMA unavailable";
      return LAYER1_RANGING;
   }

   double adx = 0.0;
   if(!AdxValue(symbol, timeframe, InpLayer1AdxPeriod, adx))
   {
      reason = "ADX unavailable";
      return LAYER1_RANGING;
   }
   if(adx < InpLayer1MinAdx)
   {
      reason = "ADX " + DoubleToString(adx, 2) + " below " + DoubleToString(InpLayer1MinAdx, 2);
      return LAYER1_RANGING;
   }

   bool higher = HasDirectionalStructure(rates, true);
   bool lower = HasDirectionalStructure(rates, false);
   double close = rates[1].close;

   if(higher && close > ema200 && ema9 > ema21 && ema21 > ema50)
   {
      reason = "HH/HL + price above EMA200 + EMA stack bullish + ADX " + DoubleToString(adx, 2);
      return LAYER1_TRENDING_UP;
   }
   if(lower && close < ema200 && ema9 < ema21 && ema21 < ema50)
   {
      reason = "LH/LL + price below EMA200 + EMA stack bearish + ADX " + DoubleToString(adx, 2);
      return LAYER1_TRENDING_DOWN;
   }

   reason = "structure/EMA alignment failed";
   return LAYER1_RANGING;
}

bool HasDirectionalStructure(const MqlRates &rates[], bool bullish)
{
   int bars = MathMax(2, InpLayer1StructureBars);
   for(int i = 1; i <= bars; i++)
   {
      if(bullish)
      {
         if(rates[i].high <= rates[i + 1].high || rates[i].low <= rates[i + 1].low)
            return false;
      }
      else
      {
         if(rates[i].high >= rates[i + 1].high || rates[i].low >= rates[i + 1].low)
            return false;
      }
   }
   return true;
}

bool EmaValue(string symbol, ENUM_TIMEFRAMES timeframe, int period, double &value)
{
   double buffer[];
   int handle = iMA(symbol, timeframe, period, 0, MODE_EMA, PRICE_CLOSE);
   if(handle == INVALID_HANDLE)
      return false;
   bool ok = CopyBuffer(handle, 0, 1, 1, buffer) == 1;
   if(ok)
      value = buffer[0];
   IndicatorRelease(handle);
   return ok;
}

bool AdxValue(string symbol, ENUM_TIMEFRAMES timeframe, int period, double &value)
{
   double buffer[];
   int handle = iADX(symbol, timeframe, period);
   if(handle == INVALID_HANDLE)
      return false;
   bool ok = CopyBuffer(handle, 0, 1, 1, buffer) == 1;
   if(ok)
      value = buffer[0];
   IndicatorRelease(handle);
   return ok;
}

bool EntryScore(string symbol, ENUM_ORDER_TYPE side, double &score)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(symbol, InpEntryTimeframe, 0, 60, rates) < 40)
   {
      Print("Score blocked ", symbol, " reason=NO_RATES side=", EnumToString(side), " error=", GetLastError());
      return false;
   }

   double rangePercent = (rates[1].high - rates[1].low) / MathMax(rates[1].close, 0.00001) * 100.0;
   if(rangePercent > InpMaxCandleRangePercent)
   {
      Print("Score blocked ", symbol, " reason=CANDLE_RANGE side=", EnumToString(side), " range=", DoubleToString(rangePercent, 3), " max=", DoubleToString(InpMaxCandleRangePercent, 3));
      return false;
   }

   double volumeRatio = VolumeRatio(rates);

   double rsiScore = side == ORDER_TYPE_BUY ? RsiBuyScore(symbol) : RsiSellScore(symbol);
   double bbScore = side == ORDER_TYPE_BUY ? BollingerBuyScore(symbol) : BollingerSellScore(symbol);
   double momentumScore = side == ORDER_TYPE_BUY ? MomentumBuyScore(symbol, rates) : MomentumSellScore(symbol, rates);
   double trendScore = TrendScore(symbol, side);
   double volumeScore = volumeRatio < InpMinVolumeRatio ? 5.0 : Clamp(35.0 + (volumeRatio - 1.0) * 45.0);

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
