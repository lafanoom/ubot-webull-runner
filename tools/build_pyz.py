"""Build the file a customer gets: one .pyz with the runner and one strategy.

    python tools/build_pyz.py examples/UBotExample.py "dist/Example Trend v1.00.pyz"
    python tools/build_pyz.py ... --source      # ship .py instead of bytecode

Bytecode (.pyc) runs only on the Python minor version that built it, so a
bytecode .pyz refuses to start on any other version and says which one it needs.
"""
import argparse
import os
import py_compile
import re
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(HERE, "ubot_runner")

MAIN = '''import sys
NEED = {need!r}
if NEED and tuple(sys.version_info[:2]) != NEED:
    sys.exit("This program needs Python %d.%d (this is %d.%d)." % (NEED + tuple(sys.version_info[:2])))
from ubot_runner.app import main
from {module} import {cls}
sys.exit(main({cls}, sys.argv[0], sys.argv[1:]))
'''


def strategy_class(path):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    found = re.findall(r"^class\s+(UBot\w+)\s*\(\s*Strategy\s*\)", src, re.M)
    if len(found) != 1:
        raise SystemExit(f"{path}: expected one 'class UBot...(Strategy)', found {len(found)}")
    m = re.search(r'^RUNNER\s*=\s*"([^"]+)"', src, re.M)
    sys.path.insert(0, HERE)
    from ubot_runner import RUNNER
    if not m or m.group(1) != RUNNER:
        raise SystemExit(f"{path}: RUNNER must be \"{RUNNER}\" (this runner)")
    return found[0]


def add(z, arc, src_path, source):
    if source:
        z.write(src_path, arc)
        return
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "x.pyc")
        py_compile.compile(src_path, cfile=out, dfile=arc, doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        z.write(out, arc[:-3] + ".pyc")


def build(strategy_path, out_path, source=False):
    cls = strategy_class(strategy_path)
    module = os.path.splitext(os.path.basename(strategy_path))[0]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    need = None if source else tuple(sys.version_info[:2])
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("__main__.py", MAIN.format(need=need, module=module, cls=cls))
        for f in sorted(os.listdir(PKG)):
            if f.endswith(".py") and f != "__main__.py":
                add(z, "ubot_runner/" + f, os.path.join(PKG, f), source)
        add(z, module + ".py", strategy_path, source)
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy")
    ap.add_argument("out")
    ap.add_argument("--source", action="store_true")
    a = ap.parse_args()
    p = build(a.strategy, a.out, a.source)
    print(p, os.path.getsize(p), "bytes")
