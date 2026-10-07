# ubot-webull-runner

The fixed layer of every trading program uBotDesign builds for **Webull (US stocks)**.
It runs on your own computer with your own Webull App Key. It is the only code that
touches your keys, the network and your orders, and it is the same in every program,
so it can be read once and trusted.

A program is two layers in one `.pyz` file:

- **this runner** - keys, login, market hours, orders, safety limits, memory, log;
- **a strategy file** `UBot<id>.py` - written from your conditions. It sees closed
  bars and asks for things (`buy`, `sell`, `move_stop`). It cannot import anything
  except `ubot_runner.strategy`.

Nothing is ever sent anywhere except to Webull. There is no telemetry.

## Run it

1. Install Python 3.12 and the Webull SDK: `pip install webull-openapi-python-sdk==3.0.2`
2. Subscribe to the free Nasdaq Basic market data **for OpenAPI** in the Webull app
   (it is separate from the app's own data).
3. `python "My Program v1.00.pyz"` - the first run writes `webull.toml` next to it.
   Put your App Key and App Secret in it and list the tickers it may trade.
4. `python "My Program v1.00.pyz" check` - read-only: account, buying power, tickers.
5. Run it. On the first run Webull sends an SMS; approve it in the Webull app within
   5 minutes. The login token stays valid while the program keeps calling Webull
   (it expires after 15 days without calls).
6. It starts in **dry run**: it writes what it would do and sends nothing. Set
   `dry_run = false` when you are ready.

The computer must stay on while you want it to trade.

## What it will and will not do

- Regular US hours only (09:30-16:00 New York, holidays and early closes included).
- Long only, whole shares, one position per ticker. Cash accounts are fine.
- **The stop rests at Webull** as a `STOP_LOSS` order (GTC, regular hours) right after
  the buy fills, so it still works when this program is off. It only ever moves up.
- **The target is watched by the program**: Webull Thailand accepts one resting sell
  per share you hold and refuses brackets/OCO, so when the price reaches the target
  the runner cancels the stop and sells. If the program is off, the target is not
  taken - the stop still protects you.
- Limits you set in `webull.toml`: dollars per buy, orders per day, open positions,
  daily loss. An empty ticker list trades nothing.
- Create a file named `STOP` next to the program to stop it sending anything new.
  Orders already resting at Webull stay there.
- A refused order is logged and dropped, never fired again. Every order is read back
  from Webull; "accepted" is not "filled". Order ids start with `ub<tag>` so the
  program only ever touches its own orders.
- Shares you sell by hand in the Webull app are noticed within a minute and the
  program follows Webull.

Files next to the program: `webull.toml` (your settings and keys - keep it private),
`<name>.db` (memory across restarts), `<name>.log`, `webull-token/` (Webull's login
token, a plain file - keep it private too), `<name>.lock` (one copy per folder).

## For developers

    python -m unittest discover tests
    python -m ubot_runner examples/UBotExample.py simulate --bars bars.json
    python tools/build_pyz.py examples/UBotExample.py "dist/Example Trend v1.00.pyz"

A bytecode `.pyz` runs only on the Python minor version that built it, and says so.
`--source` builds one that runs on any 3.11+.

Simulation runs the same engine against an in-memory broker: market orders fill at
the next bar's open, a stop touched inside a bar fills at the stop (or the open on a
gap) before any target in the same bar. It is an estimate - no spread, no slippage
beyond gaps, no partial fills.

## License

Apache License 2.0 - see [LICENSE](LICENSE).
