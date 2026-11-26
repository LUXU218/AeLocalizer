"""Check the trial dictionary against the production popup label decision.

Run with Python and Node.js. Pass the trial profile directory as the argument.
The check reads JSON and evaluates only the JavaScript label lookup.
"""
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.managed_guard import LanguageEngine
from runtime.param_draw_hook import JS


def check_profile(profile):
    engine = LanguageEngine(str(PROJECT_ROOT), str(profile), str(profile / "engine_dict"))
    if not engine._refresh_mapping():
        raise RuntimeError(engine.map_error)

    lookup = JS[JS.index("function scopedPopupLabel("):]
    lookup = lookup[:lookup.index("\n}") + 2]
    checks = r"""
const assert = require('node:assert/strict');
let SCOPE_OPTIONS = {}, SCOPE_COUNT = 0, SEEN_REV = 0;
assert.equal(scopedPopupLabel('S_Glow\x1fS_Glow-0530', 'Union'), '\u4e13\u5c5e\u7532');
assert.equal(scopedPopupLabel('S_Mosaic\x1fS_Mosaic-0530', 'Union'), null);
assert.equal(scopedPopupLabel('S_Glow\x1fS_Glow-9999', 'Union'), null);
assert.equal(scopedPopupLabel('', 'Union'), null);
assert.equal(scopedPopupLabel('', 'Screen'), '\u6ee4\u8272');
console.log('PASS: trial dictionary, scope isolation and global fallback');
"""
    source = f"const MAP = {json.dumps(engine.mapping)};\n{lookup}\n{checks}"
    subprocess.run(["node", "-"], input=source.encode("ascii"), check=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: check_trial_popup_scope.py PROFILE_DIRECTORY")
    check_profile(Path(sys.argv[1]))
