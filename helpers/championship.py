"""Championship: Multi-Rennen Punktewertung."""
import json
import uuid
from datetime import datetime

from constants import CHAMPIONSHIPS_FILE
from helpers.results import get_result

POINTS_PRESETS = {
    "F1":     [25, 18, 15, 12, 10, 8, 6, 4, 2, 1],
    "F2":     [15, 12, 10, 8, 6, 4, 3, 2, 1],
    "IMSA":   [35, 32, 30, 28, 26, 24, 22, 20, 18, 16, 14, 12, 10, 8, 6],
    "Simple": [10, 8, 6, 5, 4, 3, 2, 1],
    "Liga":   [20, 15, 12, 10, 8, 6, 4, 3, 2, 1],
}


def load_championships() -> list:
    if CHAMPIONSHIPS_FILE.exists():
        try:
            return json.loads(CHAMPIONSHIPS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def save_championships(data: list):
    CHAMPIONSHIPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHAMPIONSHIPS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_championship(cid: str) -> dict | None:
    for c in load_championships():
        if c.get("id") == cid:
            return c
    return None


def compute_standings(championship: dict) -> dict:
    """Berechnet Punktestand + Rundenergebnisse für eine Meisterschaft."""
    points_scale = championship.get("points", POINTS_PRESETS["F1"])
    rounds       = championship.get("rounds", [])
    drivers: dict = {}

    round_meta = []
    for i, filename in enumerate(rounds):
        result = get_result(filename)
        label  = f"R{i+1}"
        if result:
            label = f"R{i+1} ({result.get('track','?')})"
        round_meta.append({"num": i+1, "filename": filename, "label": label})

        if not result:
            continue
        for entry in result.get("result", []):
            name = entry.get("DriverName", "?")
            pos  = entry.get("position", 99)
            pts  = points_scale[pos - 1] if 0 < pos <= len(points_scale) else 0
            if name not in drivers:
                drivers[name] = {
                    "driver": name, "points": 0, "wins": 0,
                    "podiums": 0, "rounds": {}, "best_finish": 99,
                }
            d = drivers[name]
            d["points"]  += pts
            d["rounds"][i + 1] = {"pos": pos, "pts": pts}
            if pos == 1:  d["wins"]    += 1
            if pos <= 3:  d["podiums"] += 1
            if pos < d["best_finish"]: d["best_finish"] = pos

    standings = sorted(
        drivers.values(),
        key=lambda x: (-x["points"], x["best_finish"], -x["wins"]),
    )
    for i, s in enumerate(standings):
        s["standing"] = i + 1
        # Punkte-Gap zum Leader
        s["gap"] = standings[0]["points"] - s["points"] if i > 0 else 0

    return {"standings": standings, "round_meta": round_meta}


def create_championship(name: str, points: list) -> dict:
    champ = {
        "id":      str(uuid.uuid4())[:8],
        "name":    name,
        "points":  points,
        "rounds":  [],
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    data = load_championships()
    data.append(champ)
    save_championships(data)
    return champ


def update_championship(cid: str, name: str = None, points: list = None) -> bool:
    data = load_championships()
    for c in data:
        if c.get("id") == cid:
            if name    is not None: c["name"]   = name
            if points  is not None: c["points"] = points
            save_championships(data)
            return True
    return False


def delete_championship(cid: str) -> bool:
    data = load_championships()
    new  = [c for c in data if c.get("id") != cid]
    if len(new) == len(data):
        return False
    save_championships(new)
    return True


def add_round(cid: str, filename: str) -> bool:
    data = load_championships()
    for c in data:
        if c.get("id") == cid:
            if filename not in c["rounds"]:
                c["rounds"].append(filename)
            save_championships(data)
            return True
    return False


def remove_round(cid: str, filename: str) -> bool:
    data = load_championships()
    for c in data:
        if c.get("id") == cid:
            c["rounds"] = [r for r in c["rounds"] if r != filename]
            save_championships(data)
            return True
    return False
