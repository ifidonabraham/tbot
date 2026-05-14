#property copyright "TradingBot"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

CTrade trade;

input string InpWatchlist1 = "XAUUSD,EURUSD,GBPUSD,USDJPY,USDCHF,USDCAD,AUDUSD,NZDUSD,EURJPY,GBPJPY,EURGBP,AUDJPY,CADJPY,CHFJPY,EURCHF,EURAUD,GBPAUD";
input string InpWatchlist2 = "AUDCAD,NZDJPY,XAGUSD,EURNZD,EURCAD,GBPCAD,GBPNZD,GBPCHF,AUDNZD,AUDCHF,NZDCAD,NZDCHF,CADCHF,USDNOK,USDSEK,USDDKK";
input string InpWatchlist3 = "USDZAR,USDHKD,USDSGD,EURSEK,EURNOK,EURDKK,EURPLN,EURTRY,GBPTRY,USDTRY,USDMXN,USDPLN,USDHUF,USDTHB,USDCNH,XPTUSD,XPDUSD";
input string InpBlockedSymbols = "";
input ENUM_TIMEFRAMES InpEntryTimeframe = PERIOD_M1;
input ENUM_TIMEFRAMES InpTrendTimeframe = PERIOD_M5;
input double InpVolume = 0.01;
input double InpMaxVolume = 0.01;
input int InpMagic = 260514;
input int InpDeviationPoints = 20;
input bool InpAllowNewEntries = true;
input bool InpResetMemoryOnStart = true;

input double InpEntryThreshold = 55.0;
input int InpMaxNewPositionsPerScan = 3;
input int InpMaxTradesPerDay = 0;
input int InpMaxOpenPositions = 0;
input int InpMaxOpenPositionsPerSymbol = 0;
input double InpGridMinDistancePercent = 0.0;
input int InpMaxGridPositionsPerSide = 0;
input bool InpAllowMinVolumeGridFallback = true;

input double InpMaxDailyLossMoney = 25.0;
input double InpMaxDailyLossPercent = 20.0;
input double InpDailyLossHalfThreshold = 80.0;
input double InpDailyLossThreeQuarterThreshold = 88.0;

input double InpMaxCandleRangePercent = 1.5;
input double InpVolumeMinRatio = 0.50;
input double InpMaxSpreadPercent = 0.08;
input double InpSpreadScorePenaltyMultiplier = 200.0;
input double InpStopLossPercent = 0.75;
input double InpBreakevenTriggerPercent = 0.50;
input double InpTakeProfitPercent = 1.00;
input double InpExtendedTakeProfitPercent = 1.80;
input double InpExtendedTakeProfitHoldScore = 70.0;
input double InpTrailingActivationPercent = 0.75;
input double InpTrailingGivebackPercent = 0.35;
input double InpExitMomentumFadeScore = 42.0;

input bool InpMicroProfitExitEnabled = true;
input double InpMicroProfitMinPercent = 0.01;
input double InpMicroProfitMinMoney = 0.01;
input double InpMicroProfitFadeScore = 60.0;
input double InpMicroProfitGivebackPercent = 0.01;
input double InpMicroProfitGivebackMoney = 0.01;
input bool InpCloseOnAnyProfitDrop = true;
input double InpGroupTakeProfitMoney = 0.01;
input double InpGroupProfitGivebackMoney = 0.01;
input bool InpCloseGroupOnAnyProfitDrop = true;

input int InpPositionCheckSeconds = 1;
input int InpEntryScanSeconds = 2;

string Symbols[];
datetime LastEntryScan = 0;
datetime CurrentDay = 0;
double StartDayEquity = 0.0;
double DailyRealizedPnl = 0.0;
int DailyTradeCount = 0;

struct Setup
{
   string symbol;
   ENUM_ORDER_TYPE side;
   double score;
   double volume;
};

double Clamp(double value, double low = 0.0, double high = 100.0)
{
   return MathMax(low, MathMin(high, value));
}

string Trim(string value)
{
   StringTrimLeft(value);
   StringTrimRight(value);
   return value;
}

bool SymbolInCsv(string symbol, string csv)
{
   string raw[];
   int count = StringSplit(csv, ',', raw);
   for(int i = 0; i < count; i++)
   {
      if(Trim(raw[i]) == symbol)
         return true;
   }
   return false;
}

void ClearAllBotGlobals()
{
   for(int i = GlobalVariablesTotal() - 1; i >= 0; i--)
   {
      string name = GlobalVariableName(i);
      if(StringFind(name, "TradingBot") == 0)
         GlobalVariableDel(name);
   }
   Print("TradingBot memory reset complete.");
}

int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpDeviationPoints);
   if(InpResetMemoryOnStart)
      ClearAllBotGlobals();

   string watchlist = InpWatchlist1 + "," + InpWatchlist2 + "," + InpWatchlist3;
   string raw[];
   int count = StringSplit(watchlist, ',', raw);
   ArrayResize(Symbols, 0);
   for(int i = 0; i < count; i++)
   {
      string symbol = Trim(raw[i]);
      if(symbol == "")
         continue;
      if(!SymbolSelect(symbol, true))
         Print("Could not select symbol: ", symbol);
      int size = ArraySize(Symbols);
      ArrayResize(Symbols, size + 1);
      Symbols[size] = symbol;
   }

   ResetDailyIfNeeded();
   EventSetTimer(MathMax(1, InpPositionCheckSeconds));
   Print("TradingBot EA started. Symbols: ", ArraySize(Symbols));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTick()
{
   RunCycle();
}

void OnTimer()
{
   RunCycle();
}

void RunCycle()
{
   ResetDailyIfNeeded();
   ManagePositions();

   datetime now = TimeCurrent();
   if(now - LastEntryScan >= InpEntryScanSeconds)
   {
      ScanAndTrade();
      LastEntryScan = now;
   }
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
      StartDayEquity = AccountInfoDouble(ACCOUNT_EQUITY);
      DailyRealizedPnl = 0.0;
      DailyTradeCount = 0;
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

double ActiveEntryThreshold()
{
   double limit = DailyLossLimit();
   if(limit <= 0.0)
      return InpEntryThreshold;

   double dailyLoss = MathMax(0.0, -DailyRealizedPnl);
   if(dailyLoss >= limit)
      return -1.0;
   if(dailyLoss >= limit * 0.75)
      return InpDailyLossThreeQuarterThreshold;
   if(dailyLoss >= limit * 0.50)
      return InpDailyLossHalfThreshold;
   return InpEntryThreshold;
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

int CountBotPositionsBySide(string symbol, ENUM_POSITION_TYPE type)
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != symbol)
         continue;
      if((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) != type)
         continue;
      count++;
   }
   return count;
}

bool SymbolSideStats(string symbol, ENUM_POSITION_TYPE type, int &count, double &totalVolume, double &averagePrice, double &totalProfit, double &lowestEntry, double &highestEntry)
{
   count = 0;
   totalVolume = 0.0;
   averagePrice = 0.0;
   totalProfit = 0.0;
   lowestEntry = DBL_MAX;
   highestEntry = 0.0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != symbol)
         continue;
      if((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) != type)
         continue;

      double volume = PositionGetDouble(POSITION_VOLUME);
      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      count++;
      totalVolume += volume;
      averagePrice += entry * volume;
      totalProfit += PositionGetDouble(POSITION_PROFIT);
      lowestEntry = MathMin(lowestEntry, entry);
      highestEntry = MathMax(highestEntry, entry);
   }

   if(totalVolume <= 0.0)
      return false;
   averagePrice /= totalVolume;
   return true;
}

double GroupPeakMoney(string symbol, ENUM_POSITION_TYPE type, double currentProfit)
{
   string key = GroupPeakMoneyKey(symbol, type);
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

void SetGroupLastMoney(string symbol, ENUM_POSITION_TYPE type, double currentProfit)
{
   GlobalVariableSet(GroupLastMoneyKey(symbol, type), currentProfit);
}

bool GetGroupLastMoney(string symbol, ENUM_POSITION_TYPE type, double &lastProfit)
{
   string key = GroupLastMoneyKey(symbol, type);
   if(!GlobalVariableCheck(key))
      return false;
   lastProfit = GlobalVariableGet(key);
   return true;
}

void ClearGroupState(string symbol, ENUM_POSITION_TYPE type)
{
   string peakKey = GroupPeakMoneyKey(symbol, type);
   string lastKey = GroupLastMoneyKey(symbol, type);
   if(GlobalVariableCheck(peakKey))
      GlobalVariableDel(peakKey);
   if(GlobalVariableCheck(lastKey))
      GlobalVariableDel(lastKey);
}

bool CloseSymbolSidePositions(string symbol, ENUM_POSITION_TYPE type, string reason)
{
   bool closedAny = false;
   double realized = 0.0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != symbol)
         continue;
      if((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) != type)
         continue;

      double before = PositionGetDouble(POSITION_PROFIT);
      if(trade.PositionClose(ticket))
      {
         realized += before;
         DailyTradeCount++;
         ClearPositionState(ticket);
         closedAny = true;
         Print("Closed group leg ", symbol, " ticket=", ticket, " reason=", reason, " pnl=", DoubleToString(before, 2));
      }
      else
         Print("Group close failed ", symbol, " ticket=", ticket, " error=", GetLastError());
   }

   if(closedAny)
   {
      DailyRealizedPnl += realized;
      ClearGroupState(symbol, type);
      Print("Closed group ", symbol, " ", EnumToString(type), " reason=", reason, " totalPnl=", DoubleToString(realized, 2));
   }
   return closedAny;
}

void ManageSymbolSideGroup(string symbol, ENUM_POSITION_TYPE type)
{
   int count = 0;
   double volume = 0.0;
   double averagePrice = 0.0;
   double totalProfit = 0.0;
   double lowestEntry = 0.0;
   double highestEntry = 0.0;
   if(!SymbolSideStats(symbol, type, count, volume, averagePrice, totalProfit, lowestEntry, highestEntry))
   {
      ClearGroupState(symbol, type);
      return;
   }

   double peakMoney = GroupPeakMoney(symbol, type, totalProfit);
   double lastMoney = totalProfit;
   bool hasLastMoney = GetGroupLastMoney(symbol, type, lastMoney);
   double basis = MathMax(averagePrice * volume * SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE), 0.01);
   double groupPnlPercent = totalProfit / basis * 100.0;
   string reason = "";

   if(groupPnlPercent <= -InpStopLossPercent)
      reason = "GROUP_STOP_LOSS";
   else if(totalProfit >= InpGroupTakeProfitMoney)
   {
      if(InpCloseGroupOnAnyProfitDrop && hasLastMoney && totalProfit < lastMoney)
         reason = "GROUP_PROFIT_DROP";
      else if(peakMoney >= InpGroupTakeProfitMoney && totalProfit <= peakMoney - InpGroupProfitGivebackMoney)
         reason = "GROUP_PROFIT_GIVEBACK";
   }
   else if(peakMoney >= InpGroupTakeProfitMoney && totalProfit <= 0.0)
      reason = "GROUP_PROFIT_ERASED";

   if(reason != "")
      CloseSymbolSidePositions(symbol, type, reason);
   else
      SetGroupLastMoney(symbol, type, totalProfit);
}

void ManagePositionGroups()
{
   for(int i = 0; i < ArraySize(Symbols); i++)
   {
      ManageSymbolSideGroup(Symbols[i], POSITION_TYPE_BUY);
      ManageSymbolSideGroup(Symbols[i], POSITION_TYPE_SELL);
   }
}

double PositionProfitPercent()
{
   string symbol = PositionGetString(POSITION_SYMBOL);
   double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
   double volume = PositionGetDouble(POSITION_VOLUME);
   double contract = SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   double basis = MathMax(openPrice * volume * contract, 0.01);
   return PositionGetDouble(POSITION_PROFIT) / basis * 100.0;
}

string PeakKey(ulong ticket)
{
   return "TradingBotPeak_" + IntegerToString((long)ticket);
}

string FadeKey(ulong ticket)
{
   return "TradingBotFade_" + IntegerToString((long)ticket);
}

string LastPnlKey(ulong ticket)
{
   return "TradingBotLastPnl_" + IntegerToString((long)ticket);
}

string PeakMoneyKey(ulong ticket)
{
   return "TradingBotPeakMoney_" + IntegerToString((long)ticket);
}

string LastMoneyKey(ulong ticket)
{
   return "TradingBotLastMoney_" + IntegerToString((long)ticket);
}

string GroupPeakMoneyKey(string symbol, ENUM_POSITION_TYPE type)
{
   return "TradingBotGroupPeakMoney_" + symbol + "_" + IntegerToString((int)type);
}

string GroupLastMoneyKey(string symbol, ENUM_POSITION_TYPE type)
{
   return "TradingBotGroupLastMoney_" + symbol + "_" + IntegerToString((int)type);
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

bool SpreadAllowed(string symbol)
{
   double spread = SpreadPercent(symbol);
   if(spread > InpMaxSpreadPercent)
   {
      Print("Entry blocked by spread ", symbol, " spread%=", DoubleToString(spread, 4), " max%=", DoubleToString(InpMaxSpreadPercent, 4));
      return false;
   }
   return true;
}

double GetPeak(ulong ticket, double currentPnlPercent)
{
   string key = PeakKey(ticket);
   if(!GlobalVariableCheck(key))
      GlobalVariableSet(key, currentPnlPercent);
   double peak = GlobalVariableGet(key);
   if(currentPnlPercent > peak)
   {
      peak = currentPnlPercent;
      GlobalVariableSet(key, peak);
   }
   return peak;
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
   string peak = PeakKey(ticket);
   string fade = FadeKey(ticket);
   string lastPnl = LastPnlKey(ticket);
   string peakMoney = PeakMoneyKey(ticket);
   string lastMoney = LastMoneyKey(ticket);
   if(GlobalVariableCheck(peak))
      GlobalVariableDel(peak);
   if(GlobalVariableCheck(fade))
      GlobalVariableDel(fade);
   if(GlobalVariableCheck(lastPnl))
      GlobalVariableDel(lastPnl);
   if(GlobalVariableCheck(peakMoney))
      GlobalVariableDel(peakMoney);
   if(GlobalVariableCheck(lastMoney))
      GlobalVariableDel(lastMoney);
}

void ManagePositions()
{
   ManagePositionGroups();

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
      double pnlPercent = PositionProfitPercent();
      double peak = GetPeak(ticket, pnlPercent);
      double profitMoney = PositionGetDouble(POSITION_PROFIT);
      double peakMoney = GetPeakMoney(ticket, profitMoney);
      double exitScore = ExitMomentumScore(symbol, type);
      string lastPnlKey = LastPnlKey(ticket);
      double lastPnlPercent = pnlPercent;
      bool hasLastPnl = GlobalVariableCheck(lastPnlKey);
      if(hasLastPnl)
         lastPnlPercent = GlobalVariableGet(lastPnlKey);
      string lastMoneyKey = LastMoneyKey(ticket);
      double lastProfitMoney = profitMoney;
      bool hasLastMoney = GlobalVariableCheck(lastMoneyKey);
      if(hasLastMoney)
         lastProfitMoney = GlobalVariableGet(lastMoneyKey);
      string reason = "";

      if(pnlPercent <= -InpStopLossPercent)
         reason = "STOP_LOSS";

      if(reason == "" && pnlPercent >= InpBreakevenTriggerPercent)
         MoveStopToBreakeven(symbol, ticket, type);

      if(reason == "" && pnlPercent <= 0.0 && peak >= InpBreakevenTriggerPercent)
         reason = "BREAKEVEN_STOP";

      bool protectedMicroProfit = pnlPercent >= InpMicroProfitMinPercent || profitMoney >= InpMicroProfitMinMoney;
      if(reason == "" && InpMicroProfitExitEnabled && protectedMicroProfit)
      {
         if(InpCloseOnAnyProfitDrop && hasLastMoney && profitMoney < lastProfitMoney)
            reason = "MICRO_PROFIT_MONEY_DROP";
         else if(InpCloseOnAnyProfitDrop && hasLastPnl && pnlPercent < lastPnlPercent)
            reason = "MICRO_PROFIT_TICK_DROP";
         else if(exitScore <= InpMicroProfitFadeScore)
            reason = "MICRO_PROFIT_MOMENTUM_FADE";
         else if(peakMoney >= InpMicroProfitMinMoney && profitMoney <= peakMoney - InpMicroProfitGivebackMoney)
            reason = "MICRO_PROFIT_MONEY_GIVEBACK";
         else if(peak >= InpMicroProfitMinPercent && pnlPercent <= peak - InpMicroProfitGivebackPercent)
            reason = "MICRO_PROFIT_GIVEBACK";
      }

      if(reason == "" && InpMicroProfitExitEnabled && peakMoney >= InpMicroProfitMinMoney && profitMoney <= 0.0)
         reason = "MICRO_PROFIT_ERASED";

      if(reason == "" && peak >= InpTrailingActivationPercent && pnlPercent <= peak - InpTrailingGivebackPercent)
         reason = "TRAILING_GIVEBACK";

      if(reason == "" && pnlPercent >= InpExtendedTakeProfitPercent && exitScore < InpExtendedTakeProfitHoldScore)
         reason = "EXTENDED_TAKE_PROFIT";

      if(reason == "" && pnlPercent >= InpTakeProfitPercent && exitScore < 65.0)
         reason = "TAKE_PROFIT";

      if(reason != "")
      {
         CloseSymbolSidePositions(symbol, type, reason);
      }
      else
      {
         GlobalVariableSet(lastPnlKey, pnlPercent);
         GlobalVariableSet(lastMoneyKey, profitMoney);
      }
   }
}

void MoveStopToBreakeven(string symbol, ulong ticket, ENUM_POSITION_TYPE type)
{
   if(!PositionSelectByTicket(ticket))
      return;
   double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
   double currentSl = PositionGetDouble(POSITION_SL);
   double currentTp = PositionGetDouble(POSITION_TP);

   if(type == POSITION_TYPE_BUY && currentSl >= openPrice && currentSl != 0.0)
      return;
   if(type == POSITION_TYPE_SELL && currentSl <= openPrice && currentSl != 0.0)
      return;

   if(!trade.PositionModify(ticket, openPrice, currentTp))
      Print("Breakeven SL failed ", symbol, " ticket=", ticket, " error=", GetLastError());
}

void EnsureBrokerStop(string symbol, ulong ticket, ENUM_POSITION_TYPE type)
{
   if(!PositionSelectByTicket(ticket))
      return;
   double currentSl = PositionGetDouble(POSITION_SL);
   if(currentSl != 0.0)
      return;

   double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
   double currentTp = PositionGetDouble(POSITION_TP);
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   double distance = InpStopLossPercent / 100.0;
   double stopLoss = type == POSITION_TYPE_BUY
      ? NormalizeDouble(openPrice * (1.0 - distance), digits)
      : NormalizeDouble(openPrice * (1.0 + distance), digits);

   if(!trade.PositionModify(ticket, stopLoss, currentTp))
      Print("Initial broker SL failed ", symbol, " ticket=", ticket, " error=", GetLastError());
}

void ScanAndTrade()
{
   if(!InpAllowNewEntries)
   {
      Print("New entries paused by InpAllowNewEntries=false. Existing positions are still managed.");
      return;
   }

   double threshold = ActiveEntryThreshold();
   if(threshold < 0.0)
   {
      Print("Daily loss limit reached. New entries paused.");
      return;
   }

   Setup setups[];
   ArrayResize(setups, 0);

   for(int i = 0; i < ArraySize(Symbols); i++)
   {
      string symbol = Symbols[i];
      if(!SymbolSelect(symbol, true))
         continue;

      double buyScore = 0.0;
      double sellScore = 0.0;
      bool buyOk = EntryScore(symbol, ORDER_TYPE_BUY, buyScore);
      bool sellOk = EntryScore(symbol, ORDER_TYPE_SELL, sellScore);

      Print("Scan ", symbol, " buy=", DoubleToString(buyScore, 2), " sell=", DoubleToString(sellScore, 2), " threshold=", DoubleToString(threshold, 2));

      if(buyOk && buyScore >= threshold && EntryAllowed(symbol, ORDER_TYPE_BUY))
         AddSetup(setups, symbol, ORDER_TYPE_BUY, buyScore, TradeVolume(symbol, ORDER_TYPE_BUY));
      if(sellOk && sellScore >= threshold && EntryAllowed(symbol, ORDER_TYPE_SELL))
         AddSetup(setups, symbol, ORDER_TYPE_SELL, sellScore, TradeVolume(symbol, ORDER_TYPE_SELL));
   }

   int total = ArraySize(setups);
   if(total <= 0)
      return;

   SortSetupsByScore(setups);

   int opened = 0;
   for(int i = 0; i < total && opened < InpMaxNewPositionsPerScan; i++)
   {
      Setup best = setups[i];
      if(best.symbol == "" || best.volume <= 0.0)
         continue;

      bool ok = false;
      double stopLoss = InitialStopLoss(best.symbol, best.side);
      if(best.side == ORDER_TYPE_BUY)
         ok = trade.Buy(best.volume, best.symbol, 0.0, stopLoss, 0.0, "TradingBot BUY");
      else
         ok = trade.Sell(best.volume, best.symbol, 0.0, stopLoss, 0.0, "TradingBot SELL");

      if(ok)
      {
         opened++;
         Print("Opened ", EnumToString(best.side), " ", best.symbol, " score=", DoubleToString(best.score, 2), " volume=", DoubleToString(best.volume, 2));
      }
      else
         Print("Open failed ", best.symbol, " ", EnumToString(best.side), " error=", GetLastError());
   }
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

bool GridDistanceAllowed(string symbol, ENUM_ORDER_TYPE side)
{
   if(InpGridMinDistancePercent <= 0.0)
      return true;

   ENUM_POSITION_TYPE type = side == ORDER_TYPE_BUY ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
   int count = 0;
   double totalVolume = 0.0;
   double averagePrice = 0.0;
   double totalProfit = 0.0;
   double lowestEntry = 0.0;
   double highestEntry = 0.0;
   if(!SymbolSideStats(symbol, type, count, totalVolume, averagePrice, totalProfit, lowestEntry, highestEntry))
      return true;

   if(InpMaxGridPositionsPerSide > 0 && count >= InpMaxGridPositionsPerSide)
      return false;

   double distance = InpGridMinDistancePercent / 100.0;
   if(side == ORDER_TYPE_BUY)
   {
      double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
      return ask <= lowestEntry * (1.0 - distance);
   }

   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   return bid >= highestEntry * (1.0 + distance);
}

bool EntryAllowed(string symbol, ENUM_ORDER_TYPE side)
{
   if(SymbolInCsv(symbol, InpBlockedSymbols))
      return false;
   if(!SpreadAllowed(symbol))
      return false;
   if(InpMaxTradesPerDay > 0 && DailyTradeCount >= InpMaxTradesPerDay)
      return false;
   if(InpMaxOpenPositions > 0 && CountBotPositions("") >= InpMaxOpenPositions)
      return false;
   if(InpMaxOpenPositionsPerSymbol > 0 && CountBotPositions(symbol) >= InpMaxOpenPositionsPerSymbol)
      return false;
   if(!GridDistanceAllowed(symbol, side))
      return false;
   if(DailyLossLimit() > 0.0 && DailyRealizedPnl <= -DailyLossLimit())
      return false;
   return true;
}

double TradeVolume(string symbol, ENUM_ORDER_TYPE side)
{
   double minVol = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxVol = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   double volume = MathMin(InpVolume, InpMaxVolume);
   ENUM_POSITION_TYPE type = side == ORDER_TYPE_BUY ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
   int sameSideCount = CountBotPositionsBySide(symbol, type);
   if(sameSideCount > 0)
      volume *= MathPow(0.5, sameSideCount);

   if(volume < minVol && !InpAllowMinVolumeGridFallback)
      return 0.0;
   volume = MathMax(volume, minVol);
   volume = MathMin(volume, maxVol);
   if(step > 0.0)
      volume = MathFloor(volume / step) * step;
   return NormalizeDouble(volume, 2);
}

double InitialStopLoss(string symbol, ENUM_ORDER_TYPE side)
{
   double distance = InpStopLossPercent / 100.0;
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   if(side == ORDER_TYPE_BUY)
   {
      double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
      return NormalizeDouble(ask * (1.0 - distance), digits);
   }
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   return NormalizeDouble(bid * (1.0 + distance), digits);
}

bool EntryScore(string symbol, ENUM_ORDER_TYPE side, double &score)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(symbol, InpEntryTimeframe, 0, 60, rates) < 40)
      return false;

   double latestClose = rates[1].close;
   double confirmationOpen = rates[0].open;
   if(side == ORDER_TYPE_BUY && confirmationOpen < latestClose)
      return false;
   if(side == ORDER_TYPE_SELL && confirmationOpen > latestClose)
      return false;

   double rangePercent = (rates[1].high - rates[1].low) / MathMax(rates[1].close, 0.00001) * 100.0;
   if(rangePercent > InpMaxCandleRangePercent)
      return false;

   double volumeRatio = VolumeRatio(rates);
   if(volumeRatio < InpVolumeMinRatio)
      return false;

   double rsiScore = side == ORDER_TYPE_BUY ? RsiBuyScore(symbol) : RsiSellScore(symbol);
   double macdScore = side == ORDER_TYPE_BUY ? MacdBuyScore(symbol) : MacdSellScore(symbol);
   double bbScore = side == ORDER_TYPE_BUY ? BollingerBuyScore(symbol) : BollingerSellScore(symbol);
   string trend = TrendStatus(symbol);
   double trendScore = 0.0;
   if(side == ORDER_TYPE_BUY)
      trendScore = trend == "BULLISH" ? 100.0 : (trend == "UNKNOWN" ? 65.0 : 0.0);
   else
      trendScore = trend == "BEARISH" ? 100.0 : (trend == "UNKNOWN" ? 65.0 : 0.0);
   double volumeScore = Clamp(35.0 + (volumeRatio - 1.0) * 45.0);
   double spreadPenalty = SpreadPercent(symbol) * InpSpreadScorePenaltyMultiplier;

   score = rsiScore * 0.25 + macdScore * 0.25 + bbScore * 0.20 + volumeScore * 0.15 + trendScore * 0.15 - spreadPenalty;
   score = Clamp(score);
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

string TrendStatus(string symbol)
{
   double ema9[3], ema21[3], closeRates[3];
   ArraySetAsSeries(ema9, true);
   ArraySetAsSeries(ema21, true);
   int h9 = iMA(symbol, InpTrendTimeframe, 9, 0, MODE_EMA, PRICE_CLOSE);
   int h21 = iMA(symbol, InpTrendTimeframe, 21, 0, MODE_EMA, PRICE_CLOSE);
   if(h9 == INVALID_HANDLE || h21 == INVALID_HANDLE)
      return "UNKNOWN";
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyBuffer(h9, 0, 0, 3, ema9) < 3 || CopyBuffer(h21, 0, 0, 3, ema21) < 3 || CopyRates(symbol, InpTrendTimeframe, 0, 3, rates) < 3)
   {
      IndicatorRelease(h9);
      IndicatorRelease(h21);
      return "UNKNOWN";
   }
   IndicatorRelease(h9);
   IndicatorRelease(h21);
   if(ema9[1] > ema21[1] && rates[1].close > ema21[1] && ema9[1] >= ema9[2])
      return "BULLISH";
   return "BEARISH";
}

double RsiBuyScore(string symbol)
{
   double rsi[];
   ArraySetAsSeries(rsi, true);
   int h = iRSI(symbol, InpEntryTimeframe, 14, PRICE_CLOSE);
   if(h == INVALID_HANDLE || CopyBuffer(h, 0, 0, 5, rsi) < 5)
      return 0.0;
   IndicatorRelease(h);
   double oversold = Clamp((45.0 - rsi[1]) * 2.2);
   double turn = rsi[1] > rsi[2] ? 25.0 : 0.0;
   double speed = Clamp((rsi[1] - rsi[3]) * 3.0, 0.0, 25.0);
   return Clamp(oversold + turn + speed);
}

double RsiSellScore(string symbol)
{
   double rsi[];
   ArraySetAsSeries(rsi, true);
   int h = iRSI(symbol, InpEntryTimeframe, 14, PRICE_CLOSE);
   if(h == INVALID_HANDLE || CopyBuffer(h, 0, 0, 5, rsi) < 5)
      return 0.0;
   IndicatorRelease(h);
   double overbought = Clamp((rsi[1] - 55.0) * 2.2);
   double turn = rsi[1] < rsi[2] ? 25.0 : 0.0;
   double speed = Clamp((rsi[3] - rsi[1]) * 3.0, 0.0, 25.0);
   return Clamp(overbought + turn + speed);
}

double MacdBuyScore(string symbol)
{
   double macd[], signal[];
   ArraySetAsSeries(macd, true);
   ArraySetAsSeries(signal, true);
   int h = iMACD(symbol, InpEntryTimeframe, 12, 26, 9, PRICE_CLOSE);
   if(h == INVALID_HANDLE || CopyBuffer(h, 0, 0, 4, macd) < 4 || CopyBuffer(h, 1, 0, 4, signal) < 4)
      return 0.0;
   IndicatorRelease(h);
   double hist = macd[1] - signal[1];
   double prevHist = macd[2] - signal[2];
   double price = MathMax(SymbolInfoDouble(symbol, SYMBOL_BID), 1.0);
   double crossed = prevHist <= 0.0 && hist > 0.0 ? 35.0 : 0.0;
   double turn = hist > prevHist ? 25.0 : 0.0;
   double strength = Clamp(MathAbs(macd[1] - signal[1]) / price * 100000.0);
   return Clamp(crossed + turn + strength);
}

double MacdSellScore(string symbol)
{
   double macd[], signal[];
   ArraySetAsSeries(macd, true);
   ArraySetAsSeries(signal, true);
   int h = iMACD(symbol, InpEntryTimeframe, 12, 26, 9, PRICE_CLOSE);
   if(h == INVALID_HANDLE || CopyBuffer(h, 0, 0, 4, macd) < 4 || CopyBuffer(h, 1, 0, 4, signal) < 4)
      return 0.0;
   IndicatorRelease(h);
   double hist = macd[1] - signal[1];
   double prevHist = macd[2] - signal[2];
   double price = MathMax(SymbolInfoDouble(symbol, SYMBOL_BID), 1.0);
   double crossed = prevHist >= 0.0 && hist < 0.0 ? 35.0 : 0.0;
   double turn = hist < prevHist ? 25.0 : 0.0;
   double strength = Clamp(MathAbs(macd[1] - signal[1]) / price * 100000.0);
   return Clamp(crossed + turn + strength);
}

double BollingerBuyScore(string symbol)
{
   double middle[], upper[], lower[];
   ArraySetAsSeries(middle, true);
   ArraySetAsSeries(upper, true);
   ArraySetAsSeries(lower, true);
   int h = iBands(symbol, InpEntryTimeframe, 20, 0, 2.0, PRICE_CLOSE);
   if(h == INVALID_HANDLE || CopyBuffer(h, 0, 0, 3, middle) < 3 || CopyBuffer(h, 1, 0, 3, upper) < 3 || CopyBuffer(h, 2, 0, 3, lower) < 3)
      return 0.0;
   IndicatorRelease(h);
   double price = SymbolInfoDouble(symbol, SYMBOL_BID);
   double width = middle[1] - lower[1];
   if(width <= 0.0)
      return 0.0;
   double breach = (lower[1] - price) / width;
   double nearBand = (lower[1] * 1.01 - price) / width;
   if(breach > 0.0)
      return Clamp(70.0 + breach * 60.0);
   return Clamp(nearBand * 70.0);
}

double BollingerSellScore(string symbol)
{
   double middle[], upper[], lower[];
   ArraySetAsSeries(middle, true);
   ArraySetAsSeries(upper, true);
   ArraySetAsSeries(lower, true);
   int h = iBands(symbol, InpEntryTimeframe, 20, 0, 2.0, PRICE_CLOSE);
   if(h == INVALID_HANDLE || CopyBuffer(h, 0, 0, 3, middle) < 3 || CopyBuffer(h, 1, 0, 3, upper) < 3 || CopyBuffer(h, 2, 0, 3, lower) < 3)
      return 0.0;
   IndicatorRelease(h);
   double price = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double width = upper[1] - middle[1];
   if(width <= 0.0)
      return 0.0;
   double breach = (price - upper[1]) / width;
   double nearBand = (price - upper[1] * 0.99) / width;
   if(breach > 0.0)
      return Clamp(70.0 + breach * 60.0);
   return Clamp(nearBand * 70.0);
}

double ExitMomentumScore(string symbol, ENUM_POSITION_TYPE positionType)
{
   bool isSell = positionType == POSITION_TYPE_SELL;
   double rsiScore = isSell ? RsiSellScore(symbol) : RsiBuyScore(symbol);
   double macdScore = isSell ? MacdSellScore(symbol) : MacdBuyScore(symbol);
   string trend = TrendStatus(symbol);
   double trendScore = 0.0;
   if(isSell)
      trendScore = trend == "BEARISH" ? 100.0 : (trend == "UNKNOWN" ? 65.0 : 0.0);
   else
      trendScore = trend == "BULLISH" ? 100.0 : (trend == "UNKNOWN" ? 65.0 : 0.0);

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   double volumeScore = 50.0;
   if(CopyRates(symbol, InpEntryTimeframe, 0, 40, rates) >= 30)
      volumeScore = Clamp(35.0 + (VolumeRatio(rates) - 1.0) * 45.0);

   return rsiScore * 0.20 + macdScore * 0.30 + 50.0 * 0.20 + trendScore * 0.15 + volumeScore * 0.15;
}
