# accounting/__init__.py
"""
Accounting app - Double-entry bookkeeping for Nxentra.

This app provides:
- Account: Chart of Accounts with hierarchy
- JournalEntry: Double-entry bookkeeping entries
- JournalLine: Debit/credit lines
- AnalysisDimension: User-defined analysis dimensions
- AnalysisDimensionValue: Values within dimensions

Commands handle all mutations to ensure events are emitted.
"""

default_app_config = "accounting.apps.AccountingConfig"