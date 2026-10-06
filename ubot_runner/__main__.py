"""Development entry: python -m ubot_runner path/to/UBotX.py [run|check|simulate ...]

A delivered program is a .pyz whose own __main__ calls app.main() with its strategy.
"""
import importlib.util
import inspect
import os
import sys

from .app import main
from .strategy import Strategy


def load_strategy(path):
    spec = importlib.util.spec_from_file_location(os.path.splitext(os.path.basename(path))[0], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    found = [c for _, c in inspect.getmembers(mod, inspect.isclass)
             if issubclass(c, Strategy) and c is not Strategy and c.__module__ == mod.__name__]
    if len(found) != 1:
        raise SystemExit(f"{path}: expected exactly one Strategy class, found {len(found)}")
    return found[0]


if __name__ == "__main__":
    if len(sys.argv) < 2 or not sys.argv[1].endswith(".py"):
        raise SystemExit(__doc__)
    path = sys.argv[1]
    sys.exit(main(load_strategy(path), path, sys.argv[2:]))
