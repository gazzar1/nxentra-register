# accounts/module_registry.py
"""
Centralized module registry for Nxentra.

Modules register themselves here during Django's app startup (AppConfig.ready).
The registry provides:
- Module metadata (label, icon, category)
- Sidebar navigation items per module
- Distinction between core (always enabled) and optional modules

Categories:
    core        — Always enabled (accounting, reports, settings)
    horizontal  — Cross-industry business capabilities (sales, purchases, inventory)
    vertical    — Industry-specific applications (clinic, property)
    interaction — Input surfaces (scratchpad, integrations)
"""


class ModuleCategory:
    CORE = "core"
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    INTERACTION = "interaction"


class ModuleRegistry:
    """Singleton registry of all Nxentra modules and their navigation metadata."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._modules = {}
        return cls._instance

    def register(self, key, *, label, icon, category, order, nav_items=None):
        """
        Register a module.

        Args:
            key: Unique module identifier (e.g. "clinic", "sales")
            label: Display name
            icon: Lucide icon name (e.g. "Stethoscope")
            category: One of ModuleCategory constants
            order: Sort order in sidebar (lower = higher)
            nav_items: List of dicts with {label, href, icon, ?translation_key}
        """
        self._modules[key] = {
            "key": key,
            "label": label,
            "icon": icon,
            "category": category,
            "order": order,
            "nav_items": nav_items or [],
        }

    def get(self, key):
        return self._modules.get(key)

    def all_modules(self):
        return sorted(self._modules.values(), key=lambda m: m["order"])

    def core_modules(self):
        return [m for m in self.all_modules() if m["category"] == ModuleCategory.CORE]

    def optional_modules(self):
        return [m for m in self.all_modules() if m["category"] != ModuleCategory.CORE]

    def keys(self):
        return list(self._modules.keys())


module_registry = ModuleRegistry()
