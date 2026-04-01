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


class SidebarTab:
    WORK = "work"
    REVIEW = "review"
    SETUP = "setup"


class ModuleRegistry:
    """Singleton registry of all Nxentra modules and their navigation metadata."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._modules = {}
            cls._instance._sidebar_sections = []
        return cls._instance

    def register(self, key, *, label, icon, category, order, nav_items=None):
        """
        Register a module (for module enablement tracking).
        """
        self._modules[key] = {
            "key": key,
            "label": label,
            "icon": icon,
            "category": category,
            "order": order,
            "nav_items": nav_items or [],
        }

    def register_sidebar(self, key, *, label, icon, tab, order, module_key=None, nav_items=None):
        """
        Register a sidebar section within a tab.

        Args:
            key: Unique section identifier (e.g. "work_finance", "review_control")
            label: Display name shown in sidebar
            icon: Lucide icon name
            tab: One of SidebarTab constants ("work", "review", "setup")
            order: Sort order within the tab (lower = higher)
            module_key: Optional module key for enablement check (None = always show)
            nav_items: List of {label, href, icon, ?translation_key}
        """
        self._sidebar_sections.append({
            "key": key,
            "label": label,
            "icon": icon,
            "tab": tab,
            "order": order,
            "module_key": module_key,
            "nav_items": nav_items or [],
        })

    def get(self, key):
        return self._modules.get(key)

    def all_modules(self):
        return sorted(self._modules.values(), key=lambda m: m["order"])

    def all_sidebar_sections(self):
        return sorted(self._sidebar_sections, key=lambda s: s["order"])

    def sidebar_for_tab(self, tab):
        return sorted(
            [s for s in self._sidebar_sections if s["tab"] == tab],
            key=lambda s: s["order"],
        )

    def core_modules(self):
        return [m for m in self.all_modules() if m["category"] == ModuleCategory.CORE]

    def optional_modules(self):
        return [m for m in self.all_modules() if m["category"] != ModuleCategory.CORE]

    def keys(self):
        return list(self._modules.keys())


module_registry = ModuleRegistry()
