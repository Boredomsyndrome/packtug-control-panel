"""Harness: v3.4.6 halt semantics — soft halts keep the nav stack, hard stop it."""
import importlib.util
import pathlib

_spec = importlib.util.spec_from_file_location(
    "webctl_navd",
    pathlib.Path(__file__).resolve().parents[1] / "robot" / "navd.py")
_navd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_navd)
NavDaemon = _navd.NavDaemon

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
