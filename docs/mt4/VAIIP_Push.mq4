//+------------------------------------------------------------------+
//| VAIIP_Push.mq4 v2 — pushes account snapshot + live quotes        |
//|                                                                  |
//| MT4 is a market-data source until a broker API connects: every   |
//| push now carries (a) per-position live bid/ask + order type +    |
//| stops, and (b) a quotes array for every Market Watch symbol —    |
//| the OS marks the book to the live tape, not the open price.      |
//|                                                                  |
//| Two transport paths, use whichever works on your setup:          |
//|  1) WebRequest → POST straight to the API (Windows native MT4).  |
//|  2) File bridge → writes MQL4/Files/vaiip_push.json every tick   |
//|     of the timer; mt4_bridge.py tails that file and POSTs it —   |
//|     this is the path for MT4 under Wine/CrossOver (macOS),       |
//|     where WebRequest is broken.                                  |
//+------------------------------------------------------------------+
#property strict
#property description "Pushes account snapshot + quotes to VAIIP every PushEverySec"
#property version "2.00"

input string PushSecret    = "";   // MT4_PUSH_SECRET from Settings
input string ApiBase       = "https://vianomics.onrender.com/api/v1";
input int    PushEverySec  = 60;
input bool   TryWebRequest = true; // set false on Wine to silence 4014
input bool   PushQuotes    = true; // Market Watch live tape

int OnInit()
{
   EventSetTimer(PushEverySec);
   if (PushSecret == "")
      Print("VAIIP: PushSecret is EMPTY — set it in EA inputs");
   else
      Print("VAIIP: armed — pushes every ", PushEverySec,
            "s via file bridge", TryWebRequest ? " + webrequest" : "");
   return INIT_SUCCEEDED;
}
void OnDeinit(const int r) { EventKillTimer(); }
void OnTimer() { Push(); }
void OnTick()  { /* timer handles pushes — no tick work */ }

string JsonTime()
{
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02d",
     Year(), Month(), Day(), Hour(), Minute(), Seconds());
}

string BuildJson()
{
   string body = StringFormat(
     "{\"secret\":\"%s\",\"account\":\"%d\",\"balance\":%.2f,"
     "\"equity\":%.2f,\"currency\":\"%s\",\"positions\":[",
     PushSecret, AccountNumber(), AccountBalance(),
     AccountEquity(), AccountCurrency());
   bool first = true;
   for (int i = 0; i < OrdersTotal(); i++) {
      if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if (OrderType() != OP_BUY && OrderType() != OP_SELL) continue;
      string sym = OrderSymbol();
      if (!first) body += ",";
      first = false;
      // type/bid/ask/stops let the OS mark to the live tape and see
      // direction + protection levels, not just the open price
      body += StringFormat(
        "{\"symbol\":\"%s\",\"qty\":%.2f,\"price\":%.5f,"
        "\"profit\":%.2f,\"type\":\"%s\",\"bid\":%.5f,\"ask\":%.5f,"
        "\"stop\":%.5f,\"target\":%.5f}",
        sym, OrderLots(), OrderOpenPrice(),
        OrderProfit() + OrderSwap() + OrderCommission(),
        OrderType() == OP_BUY ? "buy" : "sell",
        MarketInfo(sym, MODE_BID), MarketInfo(sym, MODE_ASK),
        OrderStopLoss(), OrderTakeProfit());
   }
   body += "]";

   // live tape — every symbol in Market Watch
   if (PushQuotes) {
      body += ",\"quotes\":[";
      first = true;
      for (int s = 0; s < SymbolsTotal(true); s++) {
         string qsym = SymbolName(s, true);
         double bid = MarketInfo(qsym, MODE_BID);
         double ask = MarketInfo(qsym, MODE_ASK);
         if (bid <= 0) continue;
         if (!first) body += ",";
         first = false;
         body += StringFormat(
           "{\"symbol\":\"%s\",\"bid\":%.5f,\"ask\":%.5f,\"ts\":\"%s\"}",
           qsym, bid, ask, JsonTime());
      }
      body += "]";
   }
   return body + "}";
}

void Push()
{
   string body = BuildJson();

   // Path 1 — file bridge (works everywhere, incl. Wine)
   int fh = FileOpen("vaiip_push.json",
                     FILE_WRITE | FILE_TXT | FILE_ANSI);
   if (fh != INVALID_HANDLE) {
      FileWriteString(fh, body);
      FileClose(fh);
      Print("VAIIP: snapshot written to MQL4/Files/vaiip_push.json");
   }

   // Path 2 — direct HTTP (Windows-native terminals)
   if (TryWebRequest) {
      char post[], result[]; string headers;
      StringToCharArray(body, post, 0, WHOLE_ARRAY, CP_UTF8);
      headers = "Content-Type: application/json\r\n";
      string url = ApiBase + "/external/mt4/push";
      int rc = WebRequest("POST", url, headers, 5000, post,
                          result, headers);
      if (rc == -1)
         Print("VAIIP push failed err=", GetLastError(),
               " — whitelist ", ApiBase, " or use the bridge");
      else
         Print("VAIIP pushed, HTTP ", rc);
   }
}
