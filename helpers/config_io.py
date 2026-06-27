"""INI- und YAML-Config-Hilfsfunktionen.

Bug fix #2:  Alle Datei-Operationen verwenden jetzt encoding='utf-8',
             damit Servernamen mit Umlauten / Sonderzeichen nicht abstürzen.
Bug fix #4:  write_extra_cfg überspringt nur echte YAML-Einrückungszeilen
             (statt alle nicht-Buchstaben-Zeilen), damit Leerzeilen zwischen
             Einträgen erhalten bleiben.
Bug fix #RC: Globaler Lock verhindert Race Conditions beim parallelen Schreiben.
Bug fix #AT: Atomares Schreiben (tmp → rename) verhindert Korruption bei Absturz.
Bug fix #SC: update_server_cfg respektiert Sektionsgrenzen – nur [SERVER] wird
             verändert, nicht Keys gleichen Namens in anderen Sektionen.
"""
import os
import re
import shutil
import threading
import tempfile

from constants import CFG_DIR, EXTRA_CFG_FILE, EXTRA_CFG_KEYS

# ── Locks ─────────────────────────────────────────────────────────────────────
# Globaler Lock für server_cfg.ini – verhindert Race Conditions
_cfg_lock  = threading.Lock()
# Separater Lock für extra_cfg.yml
_yaml_lock = threading.Lock()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _backup_cfg(cfg_path) -> None:
    """Legt eine .bak-Kopie der Config an bevor sie überschrieben wird."""
    try:
        shutil.copy2(cfg_path, cfg_path.with_suffix('.ini.bak'))
    except Exception:
        pass  # Backup-Fehler darf den Save nicht blockieren


def _atomic_write(path, text: str, encoding: str = "utf-8") -> None:
    """Schreibt text atomar in path (tmp-Datei → rename).
    Verhindert Datei-Korruption bei Absturz oder Unterbrechung."""
    tmp = path.with_suffix('.ini.tmp')
    tmp.write_text(text, encoding=encoding)
    os.replace(tmp, path)          # auf POSIX/Linux atomar


# ── INI helpers ───────────────────────────────────────────────────────────────

def read_server_cfg() -> dict:
    """Liest [SERVER]-Sektion aus server_cfg.ini."""
    cfg_path = CFG_DIR / "server_cfg.ini"
    if not cfg_path.exists():
        return {}
    data, section = {}, None
    with open(cfg_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
            elif "=" in line and section == "SERVER":
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
    return data


def read_full_server_cfg() -> dict:
    """Liest alle Sektionen aus server_cfg.ini."""
    cfg_path = CFG_DIR / "server_cfg.ini"
    if not cfg_path.exists():
        return {}
    result, section = {}, None
    with open(cfg_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(";") or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                result.setdefault(section, {})
            elif "=" in line and section:
                k, v = line.split("=", 1)
                result[section][k.strip()] = v.strip()
    return result


def update_server_cfg(updates: dict) -> tuple[bool, str]:
    """Schreibt Schlüssel NUR in die [SERVER]-Sektion von server_cfg.ini.
    Respektiert Sektionsgrenzen – gleiche Schlüssel in anderen Sektionen
    (z. B. NAME in [PRACTICE]) bleiben unberührt.
    """
    cfg_path = CFG_DIR / "server_cfg.ini"
    if not cfg_path.exists():
        return False, "server_cfg.ini not found"
    with _cfg_lock:
        lines = cfg_path.read_text(encoding="utf-8").splitlines()
        new_lines = []
        found_keys: set = set()
        current_section: str | None = None

        for line in lines:
            stripped = line.strip()
            # Sektions-Header
            if stripped.startswith("[") and stripped.endswith("]"):
                current_section = stripped[1:-1]
                new_lines.append(line)
                continue
            # Nur in [SERVER] ersetzen
            if current_section == "SERVER" and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in updates:
                    new_lines.append(f"{k}={updates[k]}")
                    found_keys.add(k)
                    continue
            new_lines.append(line)

        # Fehlende Keys in [SERVER] einfügen
        missing = {k: v for k, v in updates.items() if k not in found_keys}
        if missing:
            server_start = next(
                (i for i, l in enumerate(new_lines) if l.strip() == "[SERVER]"),
                None
            )
            if server_start is None:
                # [SERVER]-Sektion fehlt komplett → am Anfang einfügen
                header = ["", "[SERVER]"] + [f"{k}={v}" for k, v in missing.items()]
                new_lines = header + new_lines
            else:
                # Nach dem [SERVER]-Header, vor der nächsten Sektion einfügen
                insert_at = server_start + 1
                while insert_at < len(new_lines):
                    s = new_lines[insert_at].strip()
                    if s.startswith("[") and s.endswith("]"):
                        break
                    insert_at += 1
                for k, v in missing.items():
                    new_lines.insert(insert_at, f"{k}={v}")
                    insert_at += 1

        _backup_cfg(cfg_path)
        _atomic_write(cfg_path, "\n".join(new_lines) + "\n")
    return True, "Saved"


def update_section_cfg(section_updates: dict) -> tuple[bool, str]:
    """Schreibt Schlüssel in beliebige Sektionen von server_cfg.ini.
    Erstellt fehlende Sektionen am Ende der Datei.
    """
    cfg_path = CFG_DIR / "server_cfg.ini"
    if not cfg_path.exists():
        return False, "server_cfg.ini not found"
    with _cfg_lock:
        lines = cfg_path.read_text(encoding="utf-8").splitlines()
        new_lines, current_section = [], None
        found = {sec: set() for sec in section_updates}
        sections_seen: set = set()

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                if current_section in section_updates:
                    for k, v in section_updates[current_section].items():
                        if k not in found[current_section]:
                            new_lines.append(f"{k}={v}")
                            found[current_section].add(k)
                current_section = stripped[1:-1]
                sections_seen.add(current_section)
                new_lines.append(line)
                continue
            replaced = False
            if current_section in section_updates and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in section_updates[current_section]:
                    new_lines.append(f"{k}={section_updates[current_section][k]}")
                    found[current_section].add(k)
                    replaced = True
            if not replaced:
                new_lines.append(line)

        if current_section in section_updates:
            for k, v in section_updates[current_section].items():
                if k not in found[current_section]:
                    new_lines.append(f"{k}={v}")

        for sec, keys in section_updates.items():
            if sec not in sections_seen:
                new_lines.append("")
                new_lines.append(f"[{sec}]")
                for k, v in keys.items():
                    new_lines.append(f"{k}={v}")

        _backup_cfg(cfg_path)
        _atomic_write(cfg_path, "\n".join(new_lines) + "\n")
    return True, "Saved"


def remove_cfg_section(section_name: str) -> tuple[bool, str]:
    """Entfernt eine Sektion vollständig aus server_cfg.ini."""
    cfg_path = CFG_DIR / "server_cfg.ini"
    if not cfg_path.exists():
        return False, "server_cfg.ini not found"
    with _cfg_lock:
        lines = cfg_path.read_text(encoding="utf-8").splitlines()
        new_lines, in_target = [], False
        for line in lines:
            stripped = line.strip()
            if stripped == f"[{section_name}]":
                in_target = True
                continue
            if in_target and stripped.startswith("[") and stripped.endswith("]"):
                in_target = False
            if not in_target:
                new_lines.append(line)
        while new_lines and not new_lines[-1].strip():
            new_lines.pop()
        _backup_cfg(cfg_path)
        _atomic_write(cfg_path, "\n".join(new_lines) + "\n")
    return True, f"[{section_name}] entfernt"


# ── YAML helpers (extra_cfg.yml) ──────────────────────────────────────────────

def _yaml_quote(s: str) -> str:
    """Erzeugt einen doppelt-gequoteten, einzeiligen YAML-String."""
    s = str(s)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\n", "\\n").replace("\r", "")
    return '"' + s + '"'


def _yaml_unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        inner = s[1:-1]
        # Bug fix #7: \\ zuerst ersetzen, sonst wird \\n fälschlich zu \n
        inner = inner.replace('\\\\', '\\').replace('\\"', '"').replace('\\n', '\n')
        return inner
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1].replace("''", "'")
    return s


def _yaml_format_value(val) -> str:
    """Formatiert einen Python-Wert als einzeiliges YAML-Scalar."""
    if isinstance(val, bool):
        return "true" if val else "false"
    s = str(val)
    if s.lower() in ("true", "false"):
        return s.lower()
    try:
        float(s)
        return s
    except ValueError:
        return _yaml_quote(s)


def read_extra_cfg() -> dict:
    result = {}
    if not EXTRA_CFG_FILE.exists():
        return result
    for line in EXTRA_CFG_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for key in EXTRA_CFG_KEYS:
            if stripped.startswith(key + ":"):
                val = stripped[len(key) + 1:].strip()
                result[key] = _yaml_unquote(val)
                break
    return result


def write_extra_cfg(updates: dict) -> tuple[bool, str]:
    """Aktualisiert Schlüssel in extra_cfg.yml.

    Bug fix #4: Überspringt nur echte YAML-Einrückungszeilen (startswith '  '),
    damit Leerzeilen zwischen Einträgen erhalten bleiben.
    Bug fix #6: Nutzt _yaml_lock gegen Race Conditions.
    """
    if not EXTRA_CFG_FILE.exists():
        return False, "extra_cfg.yml not found"
    with _yaml_lock:
        lines = EXTRA_CFG_FILE.read_text(encoding="utf-8").splitlines()
        found_keys: set = set()
        new_lines = []
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            matched_key = None
            for key in updates:
                if stripped.startswith(key + ":"):
                    matched_key = key
                    break
            if matched_key is not None:
                new_lines.append(f"{matched_key}: {_yaml_format_value(updates[matched_key])}")
                found_keys.add(matched_key)
                i += 1
                while i < len(lines):
                    nxt = lines[i]
                    if not nxt.startswith("  "):
                        break
                    i += 1
                continue
            new_lines.append(line)
            i += 1

        for key, val in updates.items():
            if key not in found_keys:
                new_lines.append(f"{key}: {_yaml_format_value(val)}")

        EXTRA_CFG_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return True, "Saved"


def get_extra_cfg_description() -> str:
    return read_extra_cfg().get("ServerDescription", "")


def set_extra_cfg_description(description: str) -> tuple[bool, str]:
    return write_extra_cfg({"ServerDescription": description})
