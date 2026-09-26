"""The declarative option schemas must stay consistent with the config models:
every key a module's options panel edits has to exist on its config section,
or the UI would render controls that silently write dangling keys."""

from pydantic import BaseModel

from inky_dash.config import AppConfig, FetchConfig
from inky_dash.modules import MODULES
from inky_dash.modules.options import module_meta


def iter_fields(items):
    for item in items:
        if item["kind"] == "row":
            yield from iter_fields(item["items"])
        elif item["kind"] == "field":
            yield item


def section_model(module) -> type[BaseModel]:
    assert module.config_key in AppConfig.model_fields, module.id
    model = AppConfig.model_fields[module.config_key].annotation
    assert isinstance(model, type) and issubclass(model, BaseModel)
    return model


def test_config_keys_and_field_keys_exist():
    for module in MODULES.values():
        fields = section_model(module).model_fields
        meta = module_meta(module)
        for item in iter_fields(meta["options"]):
            assert item["key"] in fields, f"{module.id}: unknown option key {item['key']!r}"


def test_visibility_conditions_reference_real_keys():
    for module in MODULES.values():
        fields = section_model(module).model_fields
        for item in module_meta(module)["options"]:
            cond = item.get("visible_if")
            if cond:
                assert cond["key"] in fields, f"{module.id}: dangling visible_if {cond['key']!r}"


def test_fetch_keys_exist():
    for module in MODULES.values():
        if module.fetch_key is not None:
            assert module.fetch_key in FetchConfig.model_fields, module.id


def test_select_choices_are_valid_config_values():
    """Every select choice must be accepted by the section model."""
    for module in MODULES.values():
        model = section_model(module)
        defaults = model()
        for item in iter_fields(module_meta(module)["options"]):
            if item["control"] != "select":
                continue
            for choice in item["choices"]:
                model.model_validate({**defaults.model_dump(), item["key"]: choice["value"]})
