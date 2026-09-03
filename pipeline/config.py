"""
Single config loader shared by every module.

    cfg = load_cfg()

Reads config/config.yaml, then overlays config/secrets.env (gitignored,
KEY=VALUE lines) into the places that need a secret so nothing sensitive has
to live in the committed YAML:

    SHEET_ID   -> cfg["sheet"]["sheet_id"]
    EMAIL_TO   -> cfg["notify"]["email_to"]
    SMTP_*     -> cfg["smtp"][...]
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"
SECRETS_PATH = ROOT / "config" / "secrets.env"


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


def load_cfg(path: Path = CONFIG_PATH) -> dict:
    cfg = yaml.safe_load(path.read_text()) or {}
    sec = load_secrets()

    sheet = cfg.setdefault("sheet", {})
    if sec.get("SHEET_ID"):
        sheet["sheet_id"] = sec["SHEET_ID"]

    notify = cfg.setdefault("notify", {})
    if sec.get("EMAIL_TO"):
        notify["email_to"] = sec["EMAIL_TO"]

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
