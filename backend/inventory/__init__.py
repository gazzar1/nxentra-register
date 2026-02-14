# inventory/__init__.py
"""
Inventory Module for Nxentra ERP.

This module provides perpetual inventory management with:
- Warehouse/location tracking
- Stock ledger (immutable movement records)
- Weighted average costing (FIFO/LIFO per item)
- Automatic COGS calculation on sales

Stock ledger is the SOURCE OF TRUTH for quantities.
Accounting entries mirror stock movements.
"""

default_app_config = "inventory.apps.InventoryConfig"
