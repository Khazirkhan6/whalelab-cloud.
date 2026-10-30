# WhaleLab Cloud v1 — PAPER ONLY

Mobile-friendly cloud paper trading prototype. **No real Binance orders, API keys, or trained AI.**

## Quick start (Python 3.10+)

```bash
pip install -r requirements.txt
export WHALELAB_TOKEN='replace-with-a-long-random-secret'
python app.py
```

Visit `http://localhost:8080` and enter the token. For Android, deploy the project to a Python-capable HTTPS host and set `WHALELAB_TOKEN` as a private environment variable in the hosting dashboard. **Do not put the token in a public repository.** Set a persistent disk for `WHALELAB_DB`, otherwise trades can disappear on redeploy or restart. The hosting platform must support an always-on web process; free-tier sleep means the trading loop also stops. Some hosts block Binance endpoints.

## Features
- Server-side public Binance Futures price polling every ~12 seconds; fallback to alternate public host.
- Simulated $1,000 and $6 accounts; stop/target checks when fresh data arrives.
- Server-side manual START, SMART STOP, CLOSE ALL.
- Stop on price-feed failure; auto-retry feed; manual START required after recovery.
- Daily loss 2%, 0.25% risk/trade, max 5 open positions, three-loss lock.
- SQLite trade log and mobile dashboard protected by a shared access token.

## Important limitations
- A demonstration strategy based on simple one-tick momentum, not an AI prediction model. Not validated for profitability.
- Virtual stops only trigger at observed polling prices; gaps can cause losses beyond stop. No exchange-side stop orders exist.
- No order book, true fill simulation, spread, funding, minimum Binance order filters, or calibrated win probabilities.
- Browser access token is kept only in tab memory. Use HTTPS; shared-secret access is basic prototype security, not production authentication.
- Database persistence requires an attached persistent disk; multiple worker processes are **not supported** (each would start a trading loop). Run **one** process/instance only.
- On process restart, the agent defaults to STOPPED. Virtual stops are not monitored while the server is down.
- Do not use real funds with this software.
