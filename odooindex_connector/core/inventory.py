from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass
class ModuleInfo:
    name: str
    shortdesc: str = ""
    version: str = ""
    author: str = ""
    website: str = ""
    license: str = ""


def _get_version(module: Any) -> str:
    if isinstance(module, dict):
        return module.get("version") or module.get("latest_version") or ""
    version = getattr(module, "version", None) or getattr(
        module, "latest_version", None
    )
    return version or ""


def module_to_dict(module: Any) -> dict:
    """Convert an Odoo record or a dict into a serialisable module dict."""
    if isinstance(module, dict):
        return {
            "name": module.get("name", ""),
            "shortdesc": module.get("shortdesc", "") or "",
            "version": _get_version(module),
            "author": module.get("author", "") or "",
            "website": module.get("website", "") or "",
            "license": module.get("license", "") or "",
        }
    return {
        "name": getattr(module, "name", "") or "",
        "shortdesc": getattr(module, "shortdesc", "") or "",
        "version": _get_version(module),
        "author": getattr(module, "author", "") or "",
        "website": getattr(module, "website", "") or "",
        "license": getattr(module, "license", "") or "",
    }


def build_inventory_payload(
    uuid: str,
    instance_name: str,
    odoo_version: str,
    target_version: str,
    modules: Iterable[Any],
) -> dict:
    return {
        "uuid": uuid,
        "name": instance_name,
        "odoo_version": odoo_version,
        "target_version": target_version,
        "modules": [module_to_dict(module) for module in modules],
    }
