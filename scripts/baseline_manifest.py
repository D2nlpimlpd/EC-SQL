from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "baselines" / "baseline_manifest.json"


def _default_metadata(system: str, model: str = "") -> Dict[str, str]:
    if system == "ecsql" or system.startswith("ecsql"):
        return {
            "implementation_type": "proposed_method",
            "official_reproduction": "n/a",
            "baseline_reference": "EC-SQL",
            "report_label": "EC-SQL",
            "baseline_note": "",
        }
    if system.startswith("no_"):
        return {
            "implementation_type": "ecsql_ablation",
            "official_reproduction": "n/a",
            "baseline_reference": "EC-SQL ablation",
            "report_label": system,
            "baseline_note": "",
        }
    return {
        "implementation_type": "",
        "official_reproduction": "",
        "baseline_reference": "",
        "report_label": system,
        "baseline_note": "",
    }


@lru_cache(maxsize=1)
def load_baseline_manifest() -> Dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return {"systems": {}, "model_notes": {}}
    with MANIFEST_PATH.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload if isinstance(payload, dict) else {"systems": {}, "model_notes": {}}


def baseline_metadata(system: str, model: str = "") -> Dict[str, str]:
    manifest = load_baseline_manifest()
    systems = manifest.get("systems") if isinstance(manifest.get("systems"), dict) else {}
    model_notes = manifest.get("model_notes") if isinstance(manifest.get("model_notes"), dict) else {}
    raw = systems.get(system) if isinstance(systems.get(system), dict) else None
    if raw is None:
        raw = _default_metadata(system, model)
    else:
        raw = {
            "implementation_type": str(raw.get("implementation_type", "")),
            "official_reproduction": str(raw.get("official_reproduction", "")),
            "baseline_reference": str(raw.get("baseline_reference", "")),
            "report_label": str(raw.get("report_label") or raw.get("display_name") or system),
            "baseline_note": str(raw.get("notes", "")),
        }

    lowered_model = (model or "").lower()
    for token, note in model_notes.items():
        if token.lower() and token.lower() in lowered_model and isinstance(note, dict):
            note_reference = str(note.get("baseline_reference", ""))
            if note_reference and note_reference not in raw.get("baseline_reference", ""):
                raw["baseline_reference"] = " + ".join(
                    part for part in [raw.get("baseline_reference", ""), note_reference] if part
                )
            if raw.get("implementation_type") in {"", "prompt_style_proxy"}:
                raw["implementation_type"] = (
                    f"{raw['implementation_type']}+local_model_baseline"
                    if raw.get("implementation_type")
                    else "local_model_baseline"
                )
            raw["official_reproduction"] = str(note.get("official_reproduction", raw.get("official_reproduction", "")))
            model_note = str(note.get("notes", ""))
            if model_note and model_note not in raw.get("baseline_note", ""):
                raw["baseline_note"] = " ".join(part for part in [raw.get("baseline_note", ""), model_note] if part)
            break

    return {key: str(value) for key, value in raw.items()}


def manifest_systems() -> list[str]:
    manifest = load_baseline_manifest()
    systems = manifest.get("systems") if isinstance(manifest.get("systems"), dict) else {}
    return sorted(str(key) for key in systems)
