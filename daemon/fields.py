"""Validation of plugin settings against the schema a plugin registers."""

from typing import Any


def coerce_field(field: dict[str, Any], value: Any) -> Any:
    """Validate and normalize one plugin setting against its schema."""
    kind = field.get("type", "text")
    label = field.get("label", field["key"])
    if kind == "toggle":
        return bool(value)
    if kind == "number":
        try:
            num = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be a number") from None
        if "min" in field and num < field["min"]:
            raise ValueError(f"{label} must be at least {field['min']}")
        if "max" in field and num > field["max"]:
            raise ValueError(f"{label} must be at most {field['max']}")
        return int(num) if num == int(num) else num
    text = "" if value is None else str(value).strip()
    if len(text) > 500 or any(ord(c) < 32 for c in text):
        raise ValueError(f"{label} is too long or has invalid characters")
    if kind == "select":
        if text not in {o["value"] for o in field.get("options", [])}:
            raise ValueError(f"{label}: choose one of the listed options")
    elif kind == "url" and text and not text.startswith(("https://", "http://")):
        raise ValueError(f"{label} must start with https:// or http://")
    return text
