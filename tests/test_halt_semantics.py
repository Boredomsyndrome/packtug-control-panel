"""Harness: v3.4.6 halt semantics — soft halts keep the nav stack, hard stop it."""
import sys
sys.path.insert(0, ".")
from robotctl.navd import NavDaemon

d = NavDaemon(sink=None, dry=True)

d.mode = "mapping"
d._do_halt("estop")
assert d.mode == "mapping", f"soft halt must keep mapping, got {d.mode}"

d.mode = "route"
d._do_halt("nav link lost")
assert d.mode == "localizing", f"soft halt must demote route->localizing, got {d.mode}"

d.mode = "localizing"
d._do_halt("nav_off")
assert d.mode == "off", f"hard halt must stop stack, got {d.mode}"

d.mode = "mapping"
d._do_halt("shutdown")
assert d.mode == "mapping", f"client restart must NOT kill the stack, got {d.mode}"

print("halt semantics OK")
