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
3. Open the program: double-click `My Program v1.00.pyz` (or `python "My Program v1.00.pyz"`).
   Its window asks for your App Key and App Secret the first time and keeps them in
   `webull.toml` next to the program. On the first connection Webull sends an SMS;
   approve it in the Webull app within 5 minutes. The login token stays valid while
   the program keeps calling Webull (it expires after 15 days without calls).
4. In **Trading settings** list the tickers it may trade and set the money limits.
5. It starts in **test mode**: it writes what it would do and sends nothing. Switch to
   live in Trading settings → Mode; the window asks you to confirm first.

`python "My Program v1.00.pyz" --console` runs it as text, without the window, and
`check` is a read-only look at the account, buying power and tickers.

The computer must stay on while you want it to trade.

## What it will and will not do

- Regular US hours only (09:30-16:00 New York, holidays and early closes included).
- Long only, whole shares, one position per ticker. Cash accounts are fine.
- **The stop rests at Webull** as a `STOP_LOSS` order (GTC, regular hours) right after
  the buy fills, so it still works when this program is off. The strategy only ever
  moves it up; new settings you save may move it either way.
- **The target is watched by the program**: Webull Thailand accepts one resting sell
  per share you hold and refuses brackets/OCO, so when the price reaches the target
  the runner cancels the stop and sells. If the program is off, the target is not
  taken - the stop still protects you.
- Limits you set in the window (or `webull.toml`): dollars per buy, dollars in use,
  orders per day, open positions, daily loss. An empty ticker list trades nothing.
- Create a file named `STOP` next to the program to stop it sending anything new.
  Orders already resting at Webull stay there.
- A refused order is logged and dropped, never fired again. Every order is read back
  from Webull; "accepted" is not "filled". Order ids start with `ub<tag>` so the
  program only ever touches its own orders.
- Shares you sell by hand in the Webull app are noticed within a minute and the
  program follows Webull.

### The window

- **Top**: running / no new buys / stopped, test or live, the market clock (New York).
  **Stop program** offers two things: *stop new buys only* (positions held now are
  still looked after until they close) or *stop everything* (asks first, then sells
  every position at market, cancels the stops at Webull and does nothing more; when
  the market is closed the sale waits for the open). The mode is kept across restarts.
- **Money**: account value, today's P/L, 30-day results, money in use against your cap.
- **Middle**: the chart of the selected ticker with its stop/entry/target, at any bar
  size from 1 minute to 1 month (it starts at the size the program decides on). Two
  switches, both remembered: **Indicators** draws the lines the program's rules read
  (on the price, or in a small pane under it), worked out from the current inputs;
  **Trades** puts an arrow where the program bought and sold (hover for the details),
  with a dotted line between the two ends of a closed trade. Or
  **Trading settings**: tickers, money limits, the program's inputs, mode, what the
  close button does, language, and the Webull keys (new keys are tested before they
  replace the old ones). Saving writes `webull.toml` and takes effect at once -
  **including on positions held now**: the program works out their stop and target
  again from the new inputs (`levels()` in the strategy) and moves the stop at
  Webull, up or down.
- **Right**: the watched tickers with prices and a **Buy** button. A buy by hand goes
  through every rule above and the same money limits, and is looked after like the
  program's own (its stop rests at Webull, its target is watched). It is marked "You".
- **Bottom**: open and closed positions (select one and **Sell**), and what the
  program did.
- **Close (✕)**: asks whether to keep running in the corner (an icon by the clock;
  click it to bring the window back) or quit. Quitting sells nothing; the stops at
  Webull keep working, targets are not watched. "Remember" makes it stop asking.

A strategy may change the standard window a little through `UI` (see
`ubot_runner/ui.py`): a subtitle, the default language, an accent colour (never green
or red, which mean money), panels to hide, up to three extra figures in the watch
list (`ui_values()`), and up to four buttons of its own (`on_button()`), which still
order only through the runner and its rules. Anything else in `UI` is refused and the
standard window is shown.

The indicator lines come from the strategy's `PLOT` list (see `ubot_runner/plot.py`):
moving averages, Bollinger bands, Donchian channels, RSI, MACD, ATR, or a line of its
own from `plot()`. A period may name an input, so the line follows the settings.

Files next to the program: `webull.toml` (your settings and keys - keep it private),
`<name>.db` (memory across restarts), `<name>.log`, `webull-token/` (Webull's login
token, a plain file - keep it private too), `<name>.lock` (one copy per folder).

## For developers

    python -m unittest discover tests
    python -m ubot_runner examples/UBotExample.py simulate --bars bars.json --inputs '{"fast": 10}'
    python tools/build_pyz.py examples/UBotExample.py "dist/Example Trend v1.00.pyz"
    python tools/window_demo.py --lang th      # the window over a fake broker

A bytecode `.pyz` runs only on the Python minor version that built it, and says so.
`--source` builds one that runs on any 3.11+.

Simulation runs the same engine against an in-memory broker: market orders fill at
the next bar's open, a stop touched inside a bar fills at the stop (or the open on a
gap) before any target in the same bar. It is an estimate - no spread, no slippage
beyond gaps, no partial fills. The last line is `[RESULT] {json}`; strategy evidence
lines are `[ACCEPT] ...` and appear only in a simulation.

## License

Apache License 2.0 - see [LICENSE](LICENSE).
