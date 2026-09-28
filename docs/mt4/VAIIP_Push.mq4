//+------------------------------------------------------------------+
//| VAIIP_Push.mq4 — pushes MT4 account snapshot to the VAIIP API    |
//| Install: MT4 → File → Open Data Folder → MQL4/Experts → save →   |
//| compile → attach to any chart. Push fires every timer seconds +   |
//| on account change.                                                |
//+------------------------------------------------------------------+
#property strict
#property description "Pushes account snapshot to VAIIP every PushEverySec"

input string PushSecret  = "";          // MT4_PUSH_SECRET from Settings
input string ApiBase     = "https://vianomics.onrender.com/api/v1";
input int    PushEverySec = 60;

int OnInit()
{
   EventSetTimer(PushEverySec);
   if (PushSecret == "")
      Print("VAIIP: PushSecret is EMPTY — set it in EA inputs");
   else
      Print("VAIIP: armed, pushing every ", PushEverySec, "s. ",
            "First push on first timer tick (WebRequest blocked in OnInit).");
   return INIT_SUCCEEDED;
}
void OnDeinit(const int r) { EventKillTimer(); }
void OnTimer() { Push(); }
void OnTick() { /* timer handles pushes — no tick work */ }

void Push()
{
   string body = StringFormat(
     "{\"secret\":\"%s\",\"account\":\"%d\",\"balance\":%.2f,"
     "\"equity\":%.2f,\"currency\":\"%s\",\"positions\":[",
     PushSecret, AccountNumber(), AccountBalance(),
     AccountEquity(), AccountCurrency());

   for (int i = 0; i < OrdersTotal(); i++) {
      if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if (i) body += ",";
      body += StringFormat(
        "{\"symbol\":\"%s\",\"qty\":%.2f,\"price\":%.5f,"
        "\"profit\":%.2f}",
        OrderSymbol(), OrderLots(), OrderOpenPrice(),
        OrderProfit() + OrderSwap() + OrderCommission());
   }
   body += "]}";

   char post[], result[]; string headers;
   StringToCharArray(body, post, 0, WHOLE_ARRAY, CP_UTF8);
   headers = "Content-Type: application/json\r\n";
   string url = ApiBase + "/external/mt4/push";
   int rc = WebRequest("POST", url, headers, 5000, post, result, headers);
   if (rc == -1)
      Print("VAIIP push failed err=", GetLastError(),
            " — check Tools→Options→EA→WebRequest whitelist for ",
            ApiBase);
   else
      Print("VAIIP pushed, HTTP ", rc);
}
