"""Persist UI configuration changes as a TOML overlay in the daemon state directory."""

import json
import logging
import os
import tomllib
from pathlib import Path
from typing import Any

logger = logging.getLogger("netcast.persist")

OVERLAY_NAME = "config.toml"


def _overlay_path() -> Path:
    from daemon.watchdog import get_state_dir

    return get_state_dir() / OVERLAY_NAME


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(str(value), ensure_ascii=False).replace("\x7f", "\\u007f")


def dumps_toml(data: dict[str, Any]) -> str:
    """Serialize ``{table: {key: scalar}}`` to TOML (all NetCast config is this shape)."""
    lines: list[str] = []
    for table, body in data.items():
        if not isinstance(body, dict):
            continue
        lines.append(f"[{table}]")
        for key, value in body.items():
            if value is not None:
                lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines)


def save_overlay(cfg_dict: dict[str, Any], path: Path | None = None) -> Path:
    path = path or _overlay_path()
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # contains passwords
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(dumps_toml(cfg_dict))
    tmp.replace(path)
    return path


def load_overlay(path: Path | None = None) -> dict[str, Any]:
    try:
        with open(path or _overlay_path(), "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("Ignoring unreadable config overlay: %s", exc)
        return {}


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, val in extra.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], val)
        else:
            out[key] = val
    return out


def apply_overlay(cfg: Any, path: Path | None = None) -> Any:
    """Return ``cfg`` with the saved overlay applied (unchanged if absent or invalid)."""
    overlay = load_overlay(path)
    if not overlay:
        return cfg
    from daemon.config import parse_config_dict

    try:
        return parse_config_dict(_merge(cfg.to_dict(), overlay))
    except Exception as exc:
        logger.warning("Ignoring invalid saved configuration: %s", exc)
        return cfg
