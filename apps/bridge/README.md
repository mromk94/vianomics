# VAIIP ⇄ IBKR bridge

Runs on your VPS next to IB Gateway. The Render API calls it over
HTTPS with a shared bearer secret — the only inbound port is 443
(Caddy). The Gateway socket never leaves localhost.

```
Render API ──HTTPS+Bearer──▶ Caddy :443 ──▶ bridge :8600 ──TWS socket──▶ IB Gateway :4002 (localhost)
```

## Why

The TWS socket API needs a running Gateway/TWS — a stateful daemon with
a GUI lifecycle and daily relogin — which a stateless cloud service
can't host. The bridge owns the `ib_insync` connection and exposes the
exact surface `IbkrAdapter` expects (same shape as `AlpacaAdapter`).

## VPS setup (Namecheap or any KVM VPS)

1. **Buy**: smallest plan with ≥2 GB RAM, Ubuntu 24.04. Namecheap's KVM
   VPS works fine — you get root + a dedicated IPv4. Note the IP; point
   an A record `gw.vesturs.com` at it in Namecheap DNS.

2. **Harden basics**:
   ```bash
   apt update && apt upgrade -y
   ufw allow OpenSSH && ufw allow 443 && ufw allow 22
   ufw enable
   adduser vaiip && usermod -aG sudo vaiip   # work as vaiip, not root
   ```

3. **Dependencies**:
   ```bash
   apt install -y openjdk-17-jre xvfb xrdp xfce4 python3-venv caddy git
   systemctl enable --now xrdp
   ```

4. **IB Gateway**: download "IB Gateway Latest" for Linux from
   interactivebrokers.com → run the installer as `vaiip` (lands in
   `~/Jts`).

5. **GUI once** — connect via RDP (Microsoft Remote Desktop / Windows
   App → VPS IP, user `vaiip`), start Gateway, log into the **paper**
   account, then in settings:
   - *API → Settings*: enable socket API, port **4002**, trusted IPs =
     `127.0.0.1`
   - uncheck "Read-Only API" **only when ready for order testing**
   Log out after — IBC drives it headless from here.

6. **IBC** (auto-relogin — mandatory, Gateway logs off ~daily):
   ```bash
   cd /opt && unzip /path/to/IBCLinux-*.zip -d ibc
   ```
   Edit `/opt/ibc/config.ini`: `IbLoginId`, `IbPassword`,
   `TradingMode=paper`, `ReadOnlyApi=yes` (flip to `no` with order
   testing), `AcceptNonBrokerageAccountWarning=yes`. See IBC docs for
   the full key list.

7. **Bridge**:
   ```bash
   sudo mkdir -p /opt/vaiip-bridge && sudo chown vaiip /opt/vaiip-bridge
   # copy this directory to the VPS (git clone or scp apps/bridge/*)
   cd /opt/vaiip-bridge
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   cp .env.example .env
   # .env: IBKR_BRIDGE_SECRET=$(openssl rand -hex 32), IBKR_PORT=4002,
   #        IBKR_READONLY=true for now
   sudo cp deploy/vaiip-ibkr-bridge.service deploy/ib-gateway.service \
       /etc/systemd/system/
   sudo cp deploy/Caddyfile /etc/caddy/Caddyfile   # edit hostname first
   sudo systemctl daemon-reload
   sudo systemctl enable --now ib-gateway vaiip-ibkr-bridge caddy
   ```

8. **Verify**:
   ```bash
   curl -H "Authorization: Bearer $SECRET" https://gw.vesturs.com/health
   # → {"connected": true, "accounts": ["DU…"], "readonly": true}
   ```

9. **API side** (Render env vars):
   ```
   IBKR_BRIDGE_URL=https://gw.vesturs.com
   IBKR_BRIDGE_SECRET=<same secret>
   EXECUTION_BROKER=ibkr          # when ready
   EXECUTION_ENABLED=false        # stays false until live approval
   ```
   Then `GET /api/v1/execution/account/ibkr` returns the real account.

10. **Enable orders (paper first)**: set `IBKR_READONLY=false` in the
    bridge `.env`, `ReadOnlyApi=no` in IBC config, restart both units,
    run one ticket through the approval chain against the **paper**
    account. Live = swap `TradingMode=live` + `IBKR_PORT=4001`, then
    `EXECUTION_ENABLED=true`.

## Operations notes

- One API session per account — don't connect a second client with the
  same clientId, and don't log into TWS desktop with the same account.
- Daily gateway restart is normal; IBC relogs automatically. Live
  accounts may need IBKR Mobile 2FA once after restart.
- `journalctl -u ib-gateway -f` / `-u vaiip-ibkr-bridge -f` for logs.
- Secrets live in `/opt/vaiip-bridge/.env` and `/opt/ibc/config.ini` —
  `chmod 600` both.
