"""Content-Management: Cars, Tracks, ZIP-Import, Presets, Entry-List, Track-Params."""
import json
import logging
import os
import re
import shutil
import zipfile
from pathlib import Path

from werkzeug.utils import secure_filename

from constants import (
    CARS_DIR, CFG_DIR, PRESETS_FILE, TRACK_PARAMS_FILE, TRACKS_DIR,
)
from helpers.config_io import read_server_cfg, update_server_cfg

logger = logging.getLogger(__name__)


# ── Cars & Tracks ─────────────────────────────────────────────────────────────

def list_cars() -> list:
    if not CARS_DIR.exists():
        return []
    return sorted(d.name for d in CARS_DIR.iterdir() if d.is_dir())


def list_tracks() -> list:
    if not TRACKS_DIR.exists():
        return []
    result = []
    for d in sorted(TRACKS_DIR.iterdir()):
        if not d.is_dir():
            continue
        layouts = sorted(s.name for s in d.iterdir() if s.is_dir() and (s / "data").exists())
        result.append({"name": d.name, "layouts": layouts})
    return result


def get_car_ui(car: str) -> dict:
    ui_path = CARS_DIR / car / "ui" / "ui_car.json"
    name = car; brand = ""
    if ui_path.exists():
        try:
            d = json.loads(ui_path.read_text(encoding="utf-8", errors="replace"))
            name  = d.get("name", car)
            brand = d.get("brand", "")
        except Exception:
            pass
    return {"id": car, "name": name, "brand": brand}


def get_track_ui(track: str, layout: str = "") -> dict:
    candidates = []
    if layout:
        candidates.append(TRACKS_DIR / track / "ui" / layout / "ui_track.json")
    candidates.append(TRACKS_DIR / track / "ui" / "ui_track.json")
    for p in candidates:
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                return {
                    "name":     d.get("name", track),
                    "length":   d.get("length", ""),
                    "pitboxes": d.get("pitboxes", ""),
                }
            except Exception:
                pass
    return {"name": track, "length": "", "pitboxes": ""}


def get_car_skins(car: str) -> list:
    skins_dir = CARS_DIR / car / "skins"
    if not skins_dir.exists():
        return []
    return sorted(s.name for s in skins_dir.iterdir() if s.is_dir())


# ── Rich content helpers ──────────────────────────────────────────────────────

def get_folder_size_mb(path: Path) -> float:
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
            elif entry.is_dir(follow_symlinks=False):
                total += sum(
                    f.stat().st_size for f in Path(entry.path).rglob("*")
                    if f.is_file()
                )
    except OSError:
        pass
    return round(total / (1024 * 1024), 1)


def validate_car(car: str) -> list:
    base = CARS_DIR / car
    issues = []
    if not (base / "collider.kn5").exists():
        issues.append("collider.kn5 fehlt")
    if not (base / "data").is_dir() and not (base / "data.acd").exists():
        issues.append("data/ oder data.acd fehlt")
    if not (base / "ui" / "ui_car.json").exists():
        issues.append("ui/ui_car.json fehlt")
    return issues


def validate_track(track: str) -> list:
    base = TRACKS_DIR / track
    issues = []
    has_ui = (base / "ui" / "ui_track.json").exists()
    if not has_ui:
        has_ui = any(
            (base / d.name / "ui" / "ui_track.json").exists()
            for d in base.iterdir() if d.is_dir()
        ) if base.exists() else False
    if not has_ui:
        issues.append("ui_track.json fehlt")
    has_surfaces = (base / "data" / "surfaces.ini").exists()
    if not has_surfaces and base.exists():
        has_surfaces = any(
            (base / d.name / "data" / "surfaces.ini").exists()
            for d in base.iterdir() if d.is_dir()
        )
    if not has_surfaces:
        issues.append("data/surfaces.ini fehlt")
    return issues


def get_car_rich(car: str, active_cars: list) -> dict:
    ui = get_car_ui(car)
    skins_dir = CARS_DIR / car / "skins"
    skin_count = 0
    if skins_dir.exists():
        try:
            skin_count = sum(1 for s in skins_dir.iterdir() if s.is_dir())
        except OSError:
            pass
    issues = validate_car(car)
    return {
        "id": car, "name": ui["name"], "brand": ui["brand"],
        "skin_count": skin_count,
        "active": car in active_cars,
        "valid": len(issues) == 0,
        "issues": issues,
    }


def get_track_rich(track_info: dict, active_track: str) -> dict:
    name = track_info["name"]
    layout = track_info["layouts"][0] if track_info["layouts"] else ""
    ui = get_track_ui(name, layout)
    issues = validate_track(name)
    return {
        "id": name, "name": ui["name"],
        "layouts": track_info["layouts"],
        "layout_count": len(track_info["layouts"]) or 1,
        "length": ui["length"], "pitboxes": ui["pitboxes"],
        "active": name == active_track,
        "valid": len(issues) == 0,
        "issues": issues,
    }


def get_car_detail(car: str, active_cars: list) -> dict:
    base = CARS_DIR / car
    ui_path = base / "ui" / "ui_car.json"
    raw = {}
    if ui_path.exists():
        try:
            raw = json.loads(ui_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            pass
    specs = raw.get("specs", {}) if isinstance(raw.get("specs"), dict) else {}
    skins = get_car_skins(car)
    skin_list = []
    for s in skins:
        sd = base / "skins" / s
        skin_list.append({
            "name": s,
            "has_livery":  (sd / "livery.png").exists(),
            "has_preview": (sd / "preview.png").exists(),
        })
    issues = validate_car(car)
    return {
        "id": car,
        "name": raw.get("name", car),
        "brand": raw.get("brand", ""),
        "description": raw.get("description", ""),
        "tags": raw.get("tags", []),
        "class": raw.get("class", ""),
        "power": specs.get("bhp", ""),
        "weight": specs.get("weight", ""),
        "torque": specs.get("torque", ""),
        "topspeed": specs.get("topspeed", ""),
        "skins": skin_list,
        "size_mb": get_folder_size_mb(base),
        "active": car in active_cars,
        "valid": len(issues) == 0,
        "issues": issues,
    }


def get_track_detail(track: str, active_track: str) -> dict:
    base = TRACKS_DIR / track
    tracks = list_tracks()
    info = next((t for t in tracks if t["name"] == track), {"name": track, "layouts": []})
    layout_details = []
    for lyt in info["layouts"]:
        ui = get_track_ui(track, lyt)
        layout_details.append({"id": lyt, "name": ui["name"],
                                "length": ui["length"], "pitboxes": ui["pitboxes"]})
    if not layout_details:
        ui = get_track_ui(track, "")
        layout_details.append({"id": "", "name": ui["name"],
                                "length": ui["length"], "pitboxes": ui["pitboxes"]})
    issues = validate_track(track)
    return {
        "id": track,
        "name": layout_details[0]["name"] if layout_details else track,
        "layouts": layout_details,
        "size_mb": get_folder_size_mb(base),
        "active": track == active_track,
        "valid": len(issues) == 0,
        "issues": issues,
    }


# ── Entry-list: lesen & schreiben ─────────────────────────────────────────────

def _empty_slot(index: int) -> dict:
    return {
        "index": index, "model": "", "skin": "", "ballast": 0,
        "restrictor": 0, "drivername": "", "team": "", "guid": "",
        "spectator": 0,
    }


def read_entry_list() -> list:
    """Liest entry_list.ini und gibt eine Liste von Slot-Dicts zurück."""
    entry_path = CFG_DIR / "entry_list.ini"
    if not entry_path.exists():
        return []
    slots, current = [], None
    try:
        for raw in entry_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.lower().startswith("[car_"):
                if current is not None:
                    slots.append(current)
                idx = len(slots)
                current = _empty_slot(idx)
            elif current is not None and "=" in line:
                key, _, val = line.partition("=")
                key = key.strip().upper()
                val = val.strip()
                if key == "MODEL":        current["model"]      = val
                elif key == "SKIN":       current["skin"]       = val
                elif key == "BALLAST":    current["ballast"]    = int(val or 0)
                elif key == "RESTRICTOR": current["restrictor"] = int(val or 0)
                elif key == "DRIVERNAME": current["drivername"] = val
                elif key == "TEAM":       current["team"]       = val
                elif key == "GUID":       current["guid"]       = val
                elif key == "SPECTATOR_MODE": current["spectator"] = int(val or 0)
        if current is not None:
            slots.append(current)
    except Exception as exc:
        logger.error("read_entry_list fehlgeschlagen: %s", exc)
    return slots


def write_entry_list_slots(slots: list) -> None:
    """Schreibt eine Slot-Liste in entry_list.ini."""
    try:
        lines = []
        for i, s in enumerate(slots):
            skin = s.get("skin", "")
            if not skin:
                model = s.get("model", "")
                skins_dir = CARS_DIR / model / "skins"
                if skins_dir.exists():
                    sk = sorted(d.name for d in skins_dir.iterdir() if d.is_dir())
                    skin = sk[0] if sk else ""
            lines += [
                f"[CAR_{i}]",
                f"MODEL={s.get('model', '')}",
                f"SKIN={skin}",
                f"SPECTATOR_MODE={s.get('spectator', 0)}",
                f"DRIVERNAME={s.get('drivername', '')}",
                f"TEAM={s.get('team', '')}",
                f"GUID={s.get('guid', '')}",
                f"BALLAST={int(s.get('ballast', 0))}",
                f"RESTRICTOR={int(s.get('restrictor', 0))}",
                "",
            ]
        (CFG_DIR / "entry_list.ini").write_text("\n".join(lines), encoding="utf-8")
    except Exception as exc:
        logger.error("write_entry_list_slots fehlgeschlagen: %s", exc)


def regen_entry_list(car_models: list, slots_per_car: int = 2, car_config: dict = None):
    """Generiert entry_list.ini aus car_models × slots_per_car (Backward-compat).

    Bug fix #9: Fehler werden jetzt geloggt statt still verschluckt.
    """
    slots = []
    for model in car_models:
        cfg_entry = (car_config or {}).get(model, {})
        skin = cfg_entry.get("skin", "")
        if not skin:
            skins_dir = CARS_DIR / model / "skins"
            if skins_dir.exists():
                sk = sorted(s.name for s in skins_dir.iterdir() if s.is_dir())
                skin = sk[0] if sk else ""
        for _ in range(max(1, min(5, slots_per_car))):
            slots.append({
                "model": model, "skin": skin,
                "ballast": cfg_entry.get("ballast", 0),
                "restrictor": cfg_entry.get("restrictor", 0),
                "drivername": "", "team": "", "guid": "", "spectator": 0,
            })
    write_entry_list_slots(slots)


def get_current_slots_per_car(car_models: list) -> int:
    """Ermittelt die aktuelle slots_per_car-Anzahl aus der entry_list.ini.

    Bug fix #8: delete_content verwendet jetzt die tatsächliche Slot-Anzahl
    statt hardzucodierter 2.
    """
    entry_path = CFG_DIR / "entry_list.ini"
    if not entry_path.exists() or not car_models:
        return 2
    try:
        content = entry_path.read_text(encoding="utf-8")
        first_car = car_models[0]
        count = content.count(f"MODEL={first_car}")
        return max(1, count)
    except Exception:
        return 2


# ── ZIP helpers ───────────────────────────────────────────────────────────────

def analyze_zip(zip_path: Path) -> tuple[dict | None, str | None]:
    items = {"cars": [], "tracks": []}
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            for n in names:
                m = re.search(r"(?:^|/)cars/([^/]+)/", n)
                if m and m.group(1) not in items["cars"]:
                    items["cars"].append(m.group(1))
                m = re.search(r"(?:^|/)tracks/([^/]+)/", n)
                if m and m.group(1) not in items["tracks"]:
                    items["tracks"].append(m.group(1))
            if not items["cars"] and not items["tracks"]:
                roots: set = set()
                for n in names:
                    parts = n.split("/")
                    if len(parts) > 1 and parts[0]:
                        roots.add(parts[0])
                for r in roots:
                    has_data  = any(n.startswith(f"{r}/data/") for n in names)
                    has_kn5   = any(n.startswith(f"{r}/") and n.endswith(".kn5") for n in names)
                    has_skins = any(n.startswith(f"{r}/skins/") for n in names)
                    if has_skins or (has_kn5 and not has_data):
                        items["cars"].append(r)
                    elif has_data or has_kn5:
                        items["tracks"].append(r)
    except Exception as e:
        return None, str(e)
    return items, None


def _zip_rel(name: str, prefix: str) -> str | None:
    m = re.search(rf"(?:^|/){re.escape(prefix)}/(.+)", name)
    if m:
        return m.group(1)
    return None


def extract_from_zip(zip_path: Path, sel_cars: list, sel_tracks: list) -> list:
    imported = []
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        has_prefix_cars   = any(re.search(r"(?:^|/)cars/",   n) for n in names)
        has_prefix_tracks = any(re.search(r"(?:^|/)tracks/", n) for n in names)

        for name in names:
            for car in sel_cars:
                rel = _zip_rel(name, f"cars/{car}") if has_prefix_cars else _zip_rel(name, car)
                if rel and not rel.endswith("/"):
                    tgt = CARS_DIR / car / rel
                    tgt.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(name) as src, open(tgt, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    key = f"cars/{car}"
                    if key not in imported:
                        imported.append(key)
            for track in sel_tracks:
                rel = _zip_rel(name, f"tracks/{track}") if has_prefix_tracks else _zip_rel(name, track)
                if rel and not rel.endswith("/"):
                    tgt = TRACKS_DIR / track / rel
                    tgt.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(name) as src, open(tgt, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    key = f"tracks/{track}"
                    if key not in imported:
                        imported.append(key)
    return imported


# ── Track params ──────────────────────────────────────────────────────────────

def auto_add_track_params(track: str):
    section = f"[{track.lower()}]"
    TRACK_PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = TRACK_PARAMS_FILE.read_text(encoding="utf-8") if TRACK_PARAMS_FILE.exists() else ""
    if section in existing:
        return
    lat, lon, tz = 0.0, 0.0, 0
    ui_path = TRACKS_DIR / track / "ui" / "ui_track.json"
    if not ui_path.exists():
        for d in ((TRACKS_DIR / track).iterdir() if (TRACKS_DIR / track).exists() else []):
            candidate = TRACKS_DIR / track / d.name / "ui" / "ui_track.json"
            if candidate.exists():
                ui_path = candidate
                break
    if ui_path.exists():
        try:
            d = json.loads(ui_path.read_text(encoding="utf-8", errors="replace"))
            city = d.get("city", track)
        except Exception:
            city = track
    else:
        city = track
    entry = f"\n{section}\nCITY={city}\nLATITUDE={lat}\nLONGITUDE={lon}\nTIMEZONE={tz}\n"
    with open(TRACK_PARAMS_FILE, "a", encoding="utf-8") as f:
        f.write(entry)


# ── Folder upload helper ──────────────────────────────────────────────────────

def secure_filename_path(rel: str) -> Path:
    parts = Path(rel.replace("\\", "/")).parts
    safe  = [secure_filename(p) for p in parts if p and p not in (".", "..")]
    return Path(*safe) if safe else Path("file")


# ── Presets ───────────────────────────────────────────────────────────────────

def load_presets() -> dict:
    if PRESETS_FILE.exists():
        try:
            return json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_presets(data: dict):
    PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRESETS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Player list (whitelist / admins / blacklist) ──────────────────────────────

def read_guid_list(path: Path) -> list:
    if not path.exists():
        return []
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def write_guid_list(path: Path, guids: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(guids) + ("\n" if guids else ""), encoding="utf-8")


def add_guid(path: Path, guid: str) -> bool:
    guids = read_guid_list(path)
    if guid not in guids:
        guids.append(guid)
        write_guid_list(path, guids)
        return True
    return False


def remove_guid(path: Path, guid: str) -> bool:
    guids = read_guid_list(path)
    if guid in guids:
        guids.remove(guid)
        write_guid_list(path, guids)
        return True
    return False
