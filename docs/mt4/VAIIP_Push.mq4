//+------------------------------------------------------------------+
//| VAIIP_Push.mq4 — pushes MT4 account snapshot to the VAIIP API    |
//|                                                                  |
//| Two transport paths, use whichever works on your setup:          |
//|  1) WebRequest → POST straight to the API (Windows native MT4).  |
//|  2) File bridge → writes MQL4/Files/vaiip_push.json every tick   |
//|     of the timer; mt4_bridge.py tails that file and POSTs it —   |
//|     this is the path for MT4 under Wine/CrossOver (macOS),       |
//|     where WebRequest is broken.                                  |
//+------------------------------------------------------------------+
#property strict
#property description "Pushes account snapshot to VAIIP every PushEverySec"

input string PushSecret    = "";   // MT4_PUSH_SECRET from Settings
input string ApiBase       = "https://vianomics.onrender.com/api/v1";
input int    PushEverySec  = 60;
input bool   TryWebRequest = true; // set false on Wine to silence 4014

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
      if (!first) body += ",";
      first = false;
      body += StringFormat(
        "{\"symbol\":\"%s\",\"qty\":%.2f,\"price\":%.5f,"
        "\"profit\":%.2f}",
        OrderSymbol(), OrderLots(), OrderOpenPrice(),
        OrderProfit() + OrderSwap() + OrderCommission());
   }
   return body + "]}";
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
