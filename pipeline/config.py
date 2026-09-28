"""
Single config loader shared by every module.

    cfg = load_cfg()

Reads config/config.yaml, then config/config.local.yaml if present
(gitignored, personal preferences that should not be committed). In the
local file, mappings merge key by key, any other value replaces the base
value, and a key ending in "+" appends to the base list instead:

    role_keywords_block+: [manager]       # add to the committed blocklist
    filters:
      salary_floor: 130000                # replace one value, keep the rest

Finally it overlays config/secrets.env (gitignored, KEY=VALUE lines) into the
places that need a secret so nothing sensitive has to live in the committed
YAML:

    SHEET_ID          -> cfg["sheet"]["sheet_id"]
    EMAIL_TO          -> cfg["notify"]["email_to"]
    LOCATION_CONTEXT  -> cfg["filters"]["location_context"]  (your metro, e.g. "Chicago, IL")
    SMTP_*            -> cfg["smtp"][...]

A config value written as a <placeholder> counts as unset.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"
LOCAL_PATH = ROOT / "config" / "config.local.yaml"
SECRETS_PATH = ROOT / "config" / "secrets.env"


def merge_local(base: dict, over: dict) -> dict:
    """Layer `over` onto `base` in place: mappings merge, "key+" appends, else replace."""
    for key, val in (over or {}).items():
        if key.endswith("+"):
            name = key[:-1]
            base[name] = list(base.get(name) or []) + list(val or [])
        elif isinstance(val, dict) and isinstance(base.get(key), dict):
            merge_local(base[key], val)
        else:
            base[key] = val
    return base


def load_secrets(path: Path = SECRETS_PATH) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for ln in path.read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def load_cfg(path: Path = CONFIG_PATH, local_path: Path | None = LOCAL_PATH) -> dict:
    cfg = yaml.safe_load(path.read_text()) or {}
    if local_path is not None and local_path.exists():
        merge_local(cfg, yaml.safe_load(local_path.read_text()) or {})
    sec = load_secrets()

    sheet = cfg.setdefault("sheet", {})
    if sec.get("SHEET_ID"):
        sheet["sheet_id"] = sec["SHEET_ID"]

    notify = cfg.setdefault("notify", {})
    if sec.get("EMAIL_TO"):
        notify["email_to"] = sec["EMAIL_TO"]

    filters = cfg.setdefault("filters", {})
    if sec.get("LOCATION_CONTEXT"):
        filters["location_context"] = sec["LOCATION_CONTEXT"]
    lc = str(filters.get("location_context") or "").strip()
    if lc.startswith("<") and lc.endswith(">"):
        filters["location_context"] = ""

    cfg["smtp"] = {
        "host": sec.get("SMTP_HOST", "localhost"),
        "port": int(sec.get("SMTP_PORT", "587")),
        "user": sec.get("SMTP_USER", ""),
        "password": sec.get("SMTP_PASS", ""),
        "sender": sec.get("SMTP_FROM", sec.get("SMTP_USER", "") or "gauntlet@localhost"),
    }
    return cfg


def db_path(cfg: dict) -> str:
    return str(ROOT / cfg["paths"]["db"])
