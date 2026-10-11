"""The attention budget lives in static/attention.js (it runs in the browser). Exercise it with Node if present."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "outside_hour" / "static" / "attention.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

SCRIPT = r"""
const A = require(process.argv[1]);
const out = {};
out.states = [0, 30000, 40000, 50000, 60000, 90000, 600000].map(ms => A.budgetState(ms));
// visible 0-10 s, hidden 10-70 s, visible 70-75 s
const m = A.createMeter(0, true, "visible");
m.setVisible(false, 10000); out.hiddenPause = m.ms(70000);
m.setVisible(true, 70000); out.afterReturn = m.ms(75000);
m.stop(75000); out.stopped = m.ms(999999);
m.resume(100000, true); out.resumed = m.ms(103000);
const away = A.createMeter(0, true, "hidden");
away.setVisible(false, 5000); away.setVisible(true, 65000); out.away = away.ms(80000);
out.fmt = [A.fmt(0), A.fmt(59999), A.fmt(61000), A.fmt(3725000)];
out.lines = [A.compareLine(52000, 0), A.compareLine(52000, 600000), A.compareLine(52000, 60000)];
console.log(JSON.stringify(out));
"""


def run():
    r = subprocess.run([NODE, "-e", SCRIPT, str(JS)], capture_output=True, text=True, timeout=20, check=True)
    return json.loads(r.stdout)


def test_budget_phases_and_dimming():
    s = run()["states"]
    assert [x["phase"] for x in s] == ["ok", "ok", "low", "low", "over", "over", "over"]
    assert s[0]["leftS"] == 60 and s[1]["leftS"] == 30 and s[4]["leftS"] == 0
    assert s[1]["dim"] == 0 and 0 < s[3]["dim"] < 0.25 and s[4]["dim"] == 0.25
    assert s[5]["dim"] > 0.25 and s[6]["dim"] == 0.45  # capped: the page never becomes unusable
    assert s[5]["overS"] == 30 and s[6]["fraction"] == 1


def test_meter_counts_only_visible_time_and_stops():
    r = run()
    assert r["hiddenPause"] == 10000      # hidden time is not screen time
    assert r["afterReturn"] == 15000
    assert r["stopped"] == 15000          # frozen after "Phone down"
    assert r["away"] == 60000             # the away meter counts only hidden time
    assert r["resumed"] == 18000          # back on the card: counting again from where it stopped


def test_formatting_and_comparison_line():
    r = run()
    assert r["fmt"] == ["0:00", "0:59", "1:01", "1:02:05"]
    assert "not yet" in r["lines"][0]
    assert "about 11 away" in r["lines"][1]
    assert r["lines"][2].startswith("Away from this screen for 1:00")
