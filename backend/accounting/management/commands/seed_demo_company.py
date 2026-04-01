"""
Seed a demo company with realistic financial data.

Creates customers, vendors, items, invoices, bills, journal entries,
and bank transactions spanning 6 months for a realistic demo environment.

Usage:
    python manage.py seed_demo_company --company-slug sony-egypt
    python manage.py seed_demo_company --company-slug sony-egypt --flush
"""
import random
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Company, CompanyMembership
from accounts.rls import rls_bypass
from projections.write_barrier import command_writes_allowed


class Command(BaseCommand):
    help = "Seed a demo company with realistic financial data (customers, vendors, items, invoices, JEs)"

    def add_arguments(self, parser):
        parser.add_argument("--company-slug", required=True, help="Company slug to seed")
        parser.add_argument("--flush", action="store_true", help="Delete existing demo data before seeding")

    @transaction.atomic
    def handle(self, *args, **options):

        slug = options["company_slug"]

        with rls_bypass():
            try:
                company = Company.objects.get(slug=slug)
            except Company.DoesNotExist:
                raise CommandError(f"Company with slug '{slug}' not found.")

            # Find an owner user for the actor context
            membership = CompanyMembership.objects.filter(
                company=company, role="OWNER", is_active=True
            ).select_related("user").first()
            if not membership:
                raise CommandError("No active OWNER found for this company.")

            user = membership.user

            # Build a fake actor context
            from accounts.authz import ActorContext
            actor = ActorContext(
                user=user,
                company=company,
                membership=membership,
                perms=frozenset(),
            )

            self.stdout.write(f"Seeding demo data for: {company.name} ({slug})")

            if options["flush"]:
                self.stdout.write(self.style.WARNING("Flushing existing demo data..."))
                self._flush(company)

            with command_writes_allowed():
                # 1. Ensure accounts exist
                accounts = self._ensure_accounts(company)

                # 2. Create customers
                customers = self._create_customers(company)

                # 3. Create vendors
                vendors = self._create_vendors(company)

                # 4. Create items
                items = self._create_items(company, accounts)

                # 5. Create exchange rates
                self._create_exchange_rates(company)

                # 6. Create journal entries spanning 6 months
                self._create_journal_entries(actor, company, accounts, customers, vendors)

            self.stdout.write(self.style.SUCCESS(
                f"Demo data seeded successfully for {company.name}!"
            ))

    def _flush(self, company):
        """Remove demo-seeded data (keeps system accounts and COA)."""
        from accounting.models import Customer, JournalEntry, JournalLine, Vendor
        from sales.models import SalesCreditNote, SalesInvoice

        # Delete in dependency order
        JournalLine.objects.filter(company=company).delete()
        JournalEntry.objects.filter(company=company).delete()
        SalesCreditNote.objects.filter(company=company).delete()
        SalesInvoice.objects.filter(company=company).delete()
        Customer.objects.filter(company=company).delete()
        Vendor.objects.filter(company=company).delete()
        self.stdout.write("  Flushed existing demo data.")

    def _ensure_accounts(self, company):
        """Return a dict of key accounts, creating any that are missing."""
        from accounting.models import Account

        # Map common role/type to accounts
        result = {}
        mappings = [
            ("cash", "ASSET", "1110", "Cash on Hand", "النقدية"),
            ("bank", "ASSET", "1120", "Bank Account", "الحساب البنكي"),
            ("ar", "ASSET", "1200", "Accounts Receivable", "المدينون"),
            ("inventory_asset", "ASSET", "1300", "Inventory", "المخزون"),
            ("ap", "LIABILITY", "2100", "Accounts Payable", "الدائنون"),
            ("vat_payable", "LIABILITY", "2200", "VAT Payable", "ضريبة القيمة المضافة"),
            ("equity", "EQUITY", "3100", "Owner Equity", "حقوق الملكية"),
            ("retained", "EQUITY", "3200", "Retained Earnings", "الأرباح المحتجزة"),
            ("sales_revenue", "REVENUE", "4100", "Sales Revenue", "إيرادات المبيعات"),
            ("service_revenue", "REVENUE", "4200", "Service Revenue", "إيرادات الخدمات"),
            ("cogs", "EXPENSE", "5100", "Cost of Goods Sold", "تكلفة البضاعة المباعة"),
            ("rent", "EXPENSE", "6100", "Rent Expense", "مصروف الإيجار"),
            ("salaries", "EXPENSE", "6200", "Salaries & Wages", "الرواتب والأجور"),
            ("utilities", "EXPENSE", "6300", "Utilities", "المرافق"),
            ("marketing", "EXPENSE", "6400", "Marketing", "التسويق"),
            ("office", "EXPENSE", "6500", "Office Supplies", "مستلزمات المكتب"),
        ]

        for key, acct_type, code, name, name_ar in mappings:
            account, _ = Account.objects.get_or_create(
                company=company,
                code=code,
                defaults={
                    "name": name,
                    "name_ar": name_ar,
                    "account_type": acct_type,
                    "is_postable": True,
                    "is_header": False,
                    "normal_balance": "DEBIT" if acct_type in ("ASSET", "EXPENSE") else "CREDIT",
                },
            )
            result[key] = account

        self.stdout.write(f"  Ensured {len(result)} accounts.")
        return result

    def _create_customers(self, company):
        """Create demo customers."""
        from accounting.models import Customer

        customers_data = [
            ("CUST-001", "Al Futtaim Group", "مجموعة الفطيم", "billing@alfuttaim.ae", "+971 4 123 4567"),
            ("CUST-002", "Majid Al Futtaim", "ماجد الفطيم", "finance@maf.ae", "+971 4 234 5678"),
            ("CUST-003", "Emaar Properties", "إعمار العقارية", "ar@emaar.ae", "+971 4 345 6789"),
            ("CUST-004", "Landmark Group", "مجموعة لاندمارك", "accounts@landmark.ae", "+971 4 456 7890"),
            ("CUST-005", "Chalhoub Group", "مجموعة شلهوب", "finance@chalhoub.com", "+971 4 567 8901"),
            ("CUST-006", "Arabian Centres", "المراكز العربية", "billing@arabiancentres.com", "+966 11 678 9012"),
            ("CUST-007", "Alshaya Group", "مجموعة الشايع", "ap@alshaya.com", "+965 2 789 0123"),
            ("CUST-008", "BinDawood Holding", "بن داود القابضة", "finance@bindawood.com", "+966 12 890 1234"),
        ]

        customers = []
        for code, name, name_ar, email, phone in customers_data:
            cust, _ = Customer.objects.get_or_create(
                company=company,
                code=code,
                defaults={
                    "name": name,
                    "name_ar": name_ar,
                    "email": email,
                    "phone": phone,
                    "status": "ACTIVE",
                },
            )
            customers.append(cust)

        self.stdout.write(f"  Created {len(customers)} customers.")
        return customers

    def _create_vendors(self, company):
        """Create demo vendors."""
        from accounting.models import Vendor

        vendors_data = [
            ("VEND-001", "DHL Express", "دي إتش إل", "invoices@dhl.com", 30),
            ("VEND-002", "Amazon Web Services", "أمازون ويب سيرفسز", "billing@aws.amazon.com", 30),
            ("VEND-003", "Office Depot", "أوفيس ديبوت", "ar@officedepot.com", 15),
            ("VEND-004", "Etisalat Business", "اتصالات بزنس", "b2b@etisalat.ae", 30),
            ("VEND-005", "ENOC", "إينوك", "corporate@enoc.com", 15),
            ("VEND-006", "Mashreq Bank", "بنك المشرق", "corporate@mashreq.com", 0),
        ]

        vendors = []
        for code, name, name_ar, email, terms in vendors_data:
            vend, _ = Vendor.objects.get_or_create(
                company=company,
                code=code,
                defaults={
                    "name": name,
                    "name_ar": name_ar,
                    "email": email,
                    "payment_terms_days": terms,
                    "status": "ACTIVE",
                },
            )
            vendors.append(vend)

        self.stdout.write(f"  Created {len(vendors)} vendors.")
        return vendors

    def _create_items(self, company, accounts):
        """Create demo items."""
        from sales.models import Item

        items_data = [
            ("ITEM-001", "Laptop Dell XPS 15", "لاب توب ديل", "3500.00", "2800.00", "INVENTORY"),
            ("ITEM-002", "Monitor LG 27\"", "شاشة إل جي", "1200.00", "900.00", "INVENTORY"),
            ("ITEM-003", "Keyboard Logitech", "كيبورد لوجيتك", "350.00", "200.00", "INVENTORY"),
            ("ITEM-004", "Consulting Hour", "ساعة استشارة", "500.00", "0", "SERVICE"),
            ("ITEM-005", "Training Session", "جلسة تدريب", "2000.00", "0", "SERVICE"),
            ("ITEM-006", "USB Cable", "كيبل يو إس بي", "50.00", "25.00", "NON_STOCK"),
        ]

        items = []
        for code, name, name_ar, price, cost, item_type in items_data:
            item, _ = Item.objects.get_or_create(
                company=company,
                code=code,
                defaults={
                    "name": name,
                    "name_ar": name_ar,
                    "item_type": item_type,
                    "default_unit_price": Decimal(price),
                    "default_cost": Decimal(cost),
                    "sales_account": accounts.get("sales_revenue"),
                    "purchase_account": accounts.get("cogs"),
                    "is_active": True,
                },
            )
            items.append(item)

        self.stdout.write(f"  Created {len(items)} items.")
        return items

    def _create_exchange_rates(self, company):
        """Create demo exchange rates for multi-currency."""
        from accounting.models import ExchangeRate

        today = date.today()
        rates = [
            ("EUR", "USD", "1.08"),
            ("GBP", "USD", "1.27"),
            ("AED", "USD", "0.2723"),
            ("SAR", "USD", "0.2667"),
            ("EGP", "USD", "0.0204"),
        ]

        count = 0
        for from_curr, to_curr, rate in rates:
            _, created = ExchangeRate.objects.get_or_create(
                company=company,
                from_currency=from_curr,
                to_currency=to_curr,
                effective_date=today - timedelta(days=30),
                defaults={"rate": Decimal(rate), "rate_type": "SPOT", "source": "Demo"},
            )
            if created:
                count += 1

        self.stdout.write(f"  Created {count} exchange rates.")

    def _create_journal_entries(self, actor, company, accounts, customers, vendors):
        """Create realistic journal entries spanning 6 months."""
        from accounting.commands import (
            create_journal_entry,
            post_journal_entry,
            save_journal_entry_complete,
        )
        from accounting.models import JournalEntry

        today = date.today()
        created_count = 0

        # Check if JEs already exist (idempotent)
        if JournalEntry.objects.filter(company=company).count() > 5:
            self.stdout.write("  Journal entries already exist — skipping.")
            return

        # Generate entries for the last 6 months
        for months_ago in range(6, 0, -1):
            month_start = today.replace(day=1) - timedelta(days=months_ago * 30)
            month_start = month_start.replace(day=1)

            # 3-5 revenue entries per month
            for _i in range(random.randint(3, 5)):
                entry_date = month_start + timedelta(days=random.randint(1, 27))
                customer = random.choice(customers)
                amount = Decimal(str(random.randint(5000, 50000)))

                result = create_journal_entry(
                    actor=actor,
                    date=entry_date,
                    memo=f"Sales to {customer.name}",
                    lines=[
                        {"account_id": accounts["ar"].id, "debit": str(amount), "credit": "0",
                         "description": f"Invoice - {customer.name}", "customer_public_id": str(customer.public_id)},
                        {"account_id": accounts["sales_revenue"].id, "debit": "0", "credit": str(amount),
                         "description": f"Revenue - {customer.name}"},
                    ],
                    kind="NORMAL",
                )
                if result.success:
                    entry = result.data
                    save_result = save_journal_entry_complete(actor, entry.id)
                    if save_result.success:
                        entry.refresh_from_db()
                        post_journal_entry(actor, entry.id)
                        created_count += 1

            # 2-4 expense entries per month
            for _i in range(random.randint(2, 4)):
                entry_date = month_start + timedelta(days=random.randint(1, 27))
                vendor = random.choice(vendors)
                expense_account = random.choice([accounts["rent"], accounts["salaries"], accounts["utilities"], accounts["marketing"], accounts["office"]])
                amount = Decimal(str(random.randint(1000, 15000)))

                result = create_journal_entry(
                    actor=actor,
                    date=entry_date,
                    memo=f"Expense - {vendor.name}",
                    lines=[
                        {"account_id": expense_account.id, "debit": str(amount), "credit": "0",
                         "description": f"{expense_account.name} - {vendor.name}"},
                        {"account_id": accounts["bank"].id, "debit": "0", "credit": str(amount),
                         "description": f"Payment to {vendor.name}"},
                    ],
                    kind="NORMAL",
                )
                if result.success:
                    entry = result.data
                    save_result = save_journal_entry_complete(actor, entry.id)
                    if save_result.success:
                        entry.refresh_from_db()
                        post_journal_entry(actor, entry.id)
                        created_count += 1

            # 1-2 customer payments per month
            for _i in range(random.randint(1, 2)):
                entry_date = month_start + timedelta(days=random.randint(10, 27))
                customer = random.choice(customers)
                amount = Decimal(str(random.randint(5000, 30000)))

                result = create_journal_entry(
                    actor=actor,
                    date=entry_date,
                    memo=f"Receipt from {customer.name}",
                    lines=[
                        {"account_id": accounts["bank"].id, "debit": str(amount), "credit": "0",
                         "description": f"Cash receipt - {customer.name}"},
                        {"account_id": accounts["ar"].id, "debit": "0", "credit": str(amount),
                         "description": f"AR payment - {customer.name}", "customer_public_id": str(customer.public_id)},
                    ],
                    kind="NORMAL",
                )
                if result.success:
                    entry = result.data
                    save_result = save_journal_entry_complete(actor, entry.id)
                    if save_result.success:
                        entry.refresh_from_db()
                        post_journal_entry(actor, entry.id)
                        created_count += 1

        self.stdout.write(f"  Created and posted {created_count} journal entries.")
