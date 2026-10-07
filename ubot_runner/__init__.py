"""uBotDesign runner for Webull (US stocks).

The fixed layer of every program the factory delivers for Webull. It is the only
code that touches the customer's keys, the network and the money. The strategy
file (UBot<id>.py) is the other layer: it receives closed bars and returns
intentions, and it can only import `ubot_runner.strategy`.

Runs on the customer's own machine with the customer's own App Key. Nothing is
ever sent anywhere except to Webull.
"""

RUNNER = "0.1.1"
