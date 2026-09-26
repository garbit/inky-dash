"""Declarative option schemas: how a module describes its settings panel.

Each module declares the controls its settings need as a list of plain-dict
items built with the helpers below; the frontend renders them generically from
GET /api/meta, so adding a panel (or an option) never touches frontend code.

Item kinds:
- ``field``: one bound control (number/text/checkbox/select/list) editing a key
  of the module's config section.
- ``row``: fields laid out side by side.
- ``note``: muted help text; ``{key}`` placeholders interpolate section values,
  plus ``{refresh_minutes}`` for the display refresh cadence.
- ``custom``: a frontend-registered widget by name, for controls too bespoke to
  describe (e.g. the location search); it edits the whole section.

Numeric fields never restate min/max: ``resolve_options`` fills them from the
Pydantic constraints of the module's config section, so limits are declared
exactly once, on the model.
"""

from __future__ import annotations

from typing import Any

from inky_dash.config import AppConfig


def when(key: str, op: str = "truthy", value: Any = None) -> dict:
    """Visibility condition on another key of the same section: ``truthy``,
    ``eq``, or ``neq``."""
    return {"key": key, "op": op, "value": value}


def _field(key: str, label: str, control: str, visible_if: dict | None, **extra: Any) -> dict:
    item = {"kind": "field", "key": key, "label": label, "control": control, **extra}
    if visible_if:
        item["visible_if"] = visible_if
    return item


def number(
    key: str, label: str, *, step: float | None = None, visible_if: dict | None = None
) -> dict:
    extra = {"step": step} if step is not None else {}
    return _field(key, label, "number", visible_if, **extra)


def checkbox(key: str, label: str, *, visible_if: dict | None = None) -> dict:
    return _field(key, label, "checkbox", visible_if)


def select(
    key: str,
    label: str,
    choices: list[tuple[Any, str]],
    *,
    visible_if: dict | None = None,
) -> dict:
    return _field(
        key,
        label,
        "select",
        visible_if,
        choices=[{"value": value, "label": text} for value, text in choices],
    )


def text(
    key: str,
    label: str,
    *,
    placeholder: str | None = None,
    nullable: bool = False,
    visible_if: dict | None = None,
) -> dict:
    extra: dict[str, Any] = {}
    if placeholder:
        extra["placeholder"] = placeholder
    if nullable:
        extra["nullable"] = True
    return _field(key, label, "text", visible_if, **extra)


def string_list(key: str, label: str, *, visible_if: dict | None = None) -> dict:
    """Comma-separated text input bound to a list-of-strings key."""
    return _field(key, label, "list", visible_if)


def row(*items: dict) -> dict:
    return {"kind": "row", "items": list(items)}


def note(text: str, *, visible_if: dict | None = None) -> dict:
    item: dict[str, Any] = {"kind": "note", "text": text}
    if visible_if:
        item["visible_if"] = visible_if
    return item


def custom(widget: str, *, label: str | None = None, visible_if: dict | None = None) -> dict:
    item: dict[str, Any] = {"kind": "custom", "widget": widget}
    if label:
        item["label"] = label
    if visible_if:
        item["visible_if"] = visible_if
    return item


def resolve_options(module: Any) -> list[dict]:
    """Copy of the module's option items with number min/max filled in from the
    Pydantic JSON schema of its config section."""
    section = AppConfig.model_fields[module.config_key].annotation
    props = section.model_json_schema().get("properties", {})

    def fill(item: dict) -> dict:
        item = dict(item)
        if item["kind"] == "row":
            item["items"] = [fill(inner) for inner in item["items"]]
        elif item.get("control") == "number":
            spec = props.get(item["key"], {})
            for src, dst in (("minimum", "min"), ("maximum", "max")):
                if src in spec and dst not in item:
                    item[dst] = spec[src]
        return item

    return [fill(item) for item in module.options]


def module_meta(module: Any) -> dict:
    """The /api/meta entry for one module."""
    return {
        "id": module.id,
        "name": module.name,
        "config_key": module.config_key,
        "fetch_key": module.fetch_key,
        "options": resolve_options(module),
    }
