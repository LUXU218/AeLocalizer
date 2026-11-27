"""Validate a reviewed S_Glow evidence record without opening After Effects."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys


FIXTURE_SHA256 = "BC6F772C37F77F7F6AF9C484C5F88DE196A3139F5D197887193982B92513CC3A"
PANEL_STAGES = {
    "baseline_layer1": (1, False),
    "baseline_layer2": (2, False),
    "enabled_layer1": (1, True),
    "switched_layer2": (2, True),
    "returned_layer1": (1, True),
    "closed_layer1": (1, True),
    "disabled_layer1": (1, False),
    "disabled_layer2": (2, False),
    "disabled_returned_layer1": (1, False),
}
LAYERS = {
    1: ("AELOC_LANGUAGE_1", "S_Glow", "Brightness", "2.000", "Screen"),
    2: ("AELOC_LANGUAGE_0", "S_Mosaic", "Pixel Frequency", "40.00", "Luma"),
}
MAPPINGS = {
    "S_Glow": "S_辉光",
    "S_Mosaic": "S_马赛克",
    "Brightness": "亮度",
    "Screen": "滤色",
    "Pixel Frequency": "像素频率",
    "Luma": "亮度",
}


def template() -> dict:
    return {
        "package_sha256": None,
        "ae_version": None,
        "session": None,
        "fixture_sha256_before": None,
        "fixture_sha256_after": None,
        "configuration": dict.fromkeys(("effect", "param_enum", "source")),
        "requests": {
            phase: dict.fromkeys(("mode", "effect", "param_enum", "intent", "source"))
            for phase in ("on", "off")
        },
        "dictionary": {"source": None, "mappings": dict.fromkeys(MAPPINGS)},
        "acknowledgements": {
            phase: dict.fromkeys(("session", "intent", "effect", "param_enum", "source"))
            for phase in ("on", "closed", "off")
        },
        "workspace": {"closed": None, "source": None},
        "native": [
            {"stage": stage, **dict.fromkeys(("screenshot", "layer", "title", "parameter", "value", "dropdown"))}
            for stage in PANEL_STAGES
        ],
    }


def _present(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _format(value, pattern: str) -> bool:
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _evidence_file(base: Path, value) -> Path | None:
    if not _present(value):
        return None
    path = (base / value).resolve()
    return path if path.is_file() and path.stat().st_size else None


def _png(data: bytes) -> bool:
    return (len(data) >= 45 and data.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR")
            and data.endswith(b"IEND\xaeB`\x82") and all(struct.unpack_from(">II", data, 16)))


def check_record(record: dict, base: Path) -> dict:
    """Compare observations to the fixed fixture and keep unknowns visible."""
    failures = []
    missing = []

    def check(key, actual, expected, *, matches=None):
        if actual is None:
            missing.append(key)
        elif not (matches(actual) if matches else actual == expected):
            failures.append(f"{key}: saw {actual!r}, expected {expected!r}")

    def check_source(key, row):
        source = row.get("source")
        check(f"{key}.source", source, "existing evidence file", matches=lambda item: _evidence_file(base, item))

    def check_channels(key, row, enabled):
        for channel in ("effect", "param_enum"):
            check(f"{key}.{channel}", row.get(channel), enabled, matches=lambda item: item is enabled)

    check("package_sha256", record.get("package_sha256"), "64 hexadecimal digits",
          matches=lambda item: _format(item, r"[0-9A-Fa-f]{64}"))
    check("ae_version", record.get("ae_version"), "four-part AE build number",
          matches=lambda item: _format(item, r"\d+\.\d+\.\d+\.\d+"))
    session = record.get("session")
    check("session", session, "AE session identity", matches=_present)
    for key in ("fixture_sha256_before", "fixture_sha256_after"):
        value = record.get(key)
        check(key, value, FIXTURE_SHA256, matches=lambda item: isinstance(item, str) and item.upper() == FIXTURE_SHA256)

    configuration = record.get("configuration") or {}
    for key in ("effect", "param_enum"):
        check(f"configuration.{key}", configuration.get(key), "boolean", matches=lambda item: isinstance(item, bool))
    check_source("configuration", configuration)

    requests = record.get("requests") or {}
    intents = {}
    for phase, enabled in (("on", True), ("off", False)):
        key = f"requests.{phase}"
        row = requests.get(phase) or {}
        intents[phase] = row.get("intent")
        check(f"{key}.mode", row.get("mode"), "on" if enabled else "stop")
        check(f"{key}.intent", intents[phase], "nonempty request identifier", matches=_present)
        check_channels(key, row, enabled)
        check_source(key, row)
    if intents["on"] is not None and intents["on"] == intents["off"]:
        failures.append("requests.off.intent: stop must have its own identifier")

    dictionary = record.get("dictionary") or {}
    check_source("dictionary", dictionary)
    for source, translation in MAPPINGS.items():
        check(f"dictionary.{source}", (dictionary.get("mappings") or {}).get(source), translation)

    acknowledgements = record.get("acknowledgements") or {}
    for phase, request_phase in (("on", "on"), ("closed", "on"), ("off", "off")):
        key = f"acknowledgements.{phase}"
        row = acknowledgements.get(phase) or {}
        check(f"{key}.session", row.get("session"), session,
              matches=lambda item: _present(session) and item == session)
        intent = intents[request_phase]
        check(f"{key}.intent", row.get("intent"), intent,
              matches=lambda item: _present(intent) and item == intent)
        check_channels(key, row, request_phase == "on")
        check_source(key, row)

    workspace = record.get("workspace") or {}
    check("workspace.closed", workspace.get("closed"), True, matches=lambda item: item is True)
    check_source("workspace", workspace)

    images = {}
    observed = {}
    unknown_stages = []
    for row in record.get("native") or []:
        stage = row.get("stage")
        if stage not in PANEL_STAGES or stage in observed:
            unknown_stages.append(stage)
        else:
            observed[stage] = row
    if unknown_stages or list(observed) != [stage for stage in PANEL_STAGES if stage in observed]:
        failures.append(f"native: stages are out of order, unknown, or duplicated ({unknown_stages!r})")

    used_images = set()
    for stage, (layer_index, enabled) in PANEL_STAGES.items():
        key = f"native.{stage}"
        row = observed.get(stage) or {}
        layer, title, parameter, value, dropdown = LAYERS[layer_index]
        image_ref = row.get("screenshot")
        if image_ref is None:
            missing.append(f"{key}.screenshot")
        else:
            image_path = _evidence_file(base, image_ref)
            image_bytes = image_path.read_bytes() if image_path and image_path.suffix.lower() == ".png" else b""
            if not _png(image_bytes):
                failures.append(f"{key}.screenshot: invalid or missing PNG {image_ref!r}")
            elif image_path in used_images:
                failures.append(f"{key}.screenshot: use a separate capture for each stage")
            else:
                used_images.add(image_path)
                images[stage] = hashlib.sha256(image_bytes).hexdigest()

        expected_titles = (title,)
        if enabled:
            expected_titles = (MAPPINGS[title],) if layer_index == 1 else (title, MAPPINGS[title])
            parameter, dropdown = MAPPINGS[parameter], MAPPINGS[dropdown]
        check(f"{key}.layer", row.get("layer"), layer)
        check(f"{key}.title", row.get("title"), expected_titles, matches=lambda item: item in expected_titles)
        check(f"{key}.parameter", row.get("parameter"), parameter)
        check(f"{key}.value", row.get("value"), value)
        check(f"{key}.dropdown", row.get("dropdown"), dropdown)

    verdict = "fail" if failures else "incomplete" if missing else "pass"
    return {"verdict": verdict, "failures": failures, "missing": missing, "screenshot_sha256": images}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    input_choice = parser.add_mutually_exclusive_group(required=True)
    input_choice.add_argument("record", type=Path, nargs="?", help="JSON evidence record")
    input_choice.add_argument("--template", action="store_true", help="Print a blank evidence record")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.template:
        print(json.dumps(template(), ensure_ascii=False, indent=2))
        return 0
    try:
        record = json.loads(args.record.read_text(encoding="utf-8-sig"))
        result = check_record(record, args.record.parent)
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        parser.error(f"invalid evidence record: {exc}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return {"pass": 0, "fail": 1, "incomplete": 2}[result["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
