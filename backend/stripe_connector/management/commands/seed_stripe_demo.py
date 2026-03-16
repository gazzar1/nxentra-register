# stripe_connector/management/commands/seed_stripe_demo.py
"""
Seed realistic Stripe demo data for the reconciliation dashboard.

Creates a Stripe account, charges, refunds, payouts, and payout transactions
so the Stripe reconciliation page works immediately.

Usage:
    python manage.py seed_stripe_demo
    python manage.py seed_stripe_demo --company-id 1
    python manage.py seed_stripe_demo --flush
"""

import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Company
from accounting.models import Account
from accounting.mappings import ModuleAccountMapping
from projections.write_barrier import projection_writes_allowed
from stripe_connector.models import (
    StripeAccount,
    StripeCharge,
    StripeRefund,
    StripePayout,
    StripePayoutTransaction,
)

# Realistic charge descriptions
CHARGE_DESCRIPTIONS = [
    "Premium Plan - Monthly",
    "Pro Plan - Monthly",
    "Enterprise Plan - Annual",
    "Starter Plan - Monthly",
    "Team Plan - Monthly",
    "API Usage Overage",
    "Custom Integration Setup",
    "Priority Support Add-on",
    "Data Export - Large",
    "White-label License",
    "Consulting Session (1hr)",
    "Workshop Registration",
    "E-book Bundle",
    "Annual Conference Ticket",
    "Plugin License - Standard",
]

CHARGE_AMOUNTS = [
    Decimal("29.00"), Decimal("49.00"), Decimal("99.00"), Decimal("149.00"),
    Decimal("199.00"), Decimal("299.00"), Decimal("499.00"), Decimal("19.00"),
    Decimal("79.00"), Decimal("39.00"), Decimal("249.00"), Decimal("599.00"),
    Decimal("9.99"), Decimal("14.99"), Decimal("59.00"),
]

DEMO_ACCOUNTS = [
    ("SALES_REVENUE", "4100", "Sales Revenue", "REVENUE", "SALES"),
    ("STRIPE_CLEARING", "1160", "Stripe Clearing", "ASSET", "LIQUIDITY"),
    ("PAYMENT_PROCESSING_FEES", "5200", "Payment Processing Fees", "EXPENSE", "OPERATING_EXPENSE"),
    ("SALES_TAX_PAYABLE", "2200", "Sales Tax Payable", "LIABILITY", "TAX_PAYABLE"),
    ("CASH_BANK", "1100", "Cash and Bank", "ASSET", "LIQUIDITY"),
    ("CHARGEBACK_EXPENSE", "5210", "Chargeback Expense", "EXPENSE", "OTHER_EXPENSE"),
]

CUSTOMER_NAMES = [
    "Acme Corp", "TechStart Inc", "Global Widgets", "DataFlow Systems",
    "CloudNine SaaS", "Pinnacle Software", "Vertex Analytics", "Horizon Labs",
    "Nexus Digital", "Summit Solutions", "Atlas Ventures", "Quantum Logic",
]


class Command(BaseCommand):
    help = "Seed realistic Stripe demo data for the reconciliation dashboard"

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-id", type=int, default=None,
            help="Company ID to seed data for (defaults to first company)",
        )
        parser.add_argument(
            "--flush", action="store_true",
            help="Delete existing Stripe demo data before seeding",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        with projection_writes_allowed():
            self._handle(options)

    def _handle(self, options):
        if options["company_id"]:
            company = Company.objects.get(id=options["company_id"])
        else:
            company = Company.objects.first()
            if not company:
                self.stderr.write(self.style.ERROR("No companies found."))
                return

        self.stdout.write(f"Seeding Stripe demo data for: {company.name}")

        if options["flush"]:
            self._flush(company)

        self._ensure_accounts(company)
        account = self._create_account(company)
        charges = self._create_charges(company, account)
        self._create_refund(company, charges)
        self._create_payouts(company, account, charges)

        self.stdout.write(self.style.SUCCESS(
            f"Done! Created {len(charges)} charges, 1 refund, and payouts. "
            f"Visit /stripe/reconciliation to see the dashboard."
        ))

    def _flush(self, company):
        count, _ = StripePayoutTransaction.objects.filter(company=company).delete()
        self.stdout.write(f"  Deleted {count} payout transactions")
        count, _ = StripePayout.objects.filter(company=company).delete()
        self.stdout.write(f"  Deleted {count} payouts")
        count, _ = StripeRefund.objects.filter(company=company).delete()
        self.stdout.write(f"  Deleted {count} refunds")
        count, _ = StripeCharge.objects.filter(company=company).delete()
        self.stdout.write(f"  Deleted {count} charges")
        count, _ = StripeAccount.objects.filter(company=company).delete()
        self.stdout.write(f"  Deleted {count} accounts")

    def _ensure_accounts(self, company):
        for role, code, name, acct_type, acct_role in DEMO_ACCOUNTS:
            account, created = Account.objects.get_or_create(
                company=company,
                code=code,
                defaults={
                    "name": name,
                    "account_type": acct_type,
                    "role": acct_role,
                    "ledger_domain": "FINANCIAL",
                    "status": "ACTIVE",
                },
            )
            if created:
                self.stdout.write(f"  Created account {code} - {name}")

            ModuleAccountMapping.objects.update_or_create(
                company=company,
                module="stripe_connector",
                role=role,
                defaults={"account": account},
            )

    def _create_account(self, company):
        account, created = StripeAccount.objects.get_or_create(
            company=company,
            stripe_account_id="acct_demo_nxentra",
            defaults={
                "display_name": "Nxentra Demo (Stripe)",
                "status": StripeAccount.Status.ACTIVE,
                "livemode": False,
                "webhook_secret": "whsec_demo_not_real",
            },
        )
        if created:
            self.stdout.write(f"  Created Stripe account: acct_demo_nxentra")
        return account

    def _create_charges(self, company, account):
        today = date.today()
        charges = []

        for i in range(25):
            days_ago = random.randint(1, 45)
            charge_date = today - timedelta(days=days_ago)
            desc = random.choice(CHARGE_DESCRIPTIONS)
            amount = random.choice(CHARGE_AMOUNTS)
            # Stripe fee: 2.9% + $0.30
            fee = (amount * Decimal("0.029") + Decimal("0.30")).quantize(Decimal("0.01"))
            net = amount - fee
            customer = random.choice(CUSTOMER_NAMES)

            charge, created = StripeCharge.objects.get_or_create(
                company=company,
                stripe_charge_id=f"ch_demo_{i:04d}",
                defaults={
                    "account": account,
                    "stripe_payment_intent_id": f"pi_demo_{i:04d}",
                    "amount": amount,
                    "fee": fee,
                    "net": net,
                    "currency": "USD",
                    "description": desc,
                    "customer_email": f"{customer.lower().replace(' ', '.')}@example.com",
                    "customer_name": customer,
                    "charge_date": charge_date,
                    "stripe_created_at": datetime.combine(
                        charge_date, datetime.min.time(), tzinfo=timezone.utc
                    ),
                    "status": "PROCESSED",
                },
            )
            if created:
                charges.append(charge)

        self.stdout.write(f"  Created {len(charges)} charges")
        return charges

    def _create_refund(self, company, charges):
        if len(charges) < 3:
            return None

        charge = charges[2]
        refund, created = StripeRefund.objects.get_or_create(
            company=company,
            stripe_refund_id="re_demo_0001",
            defaults={
                "charge": charge,
                "amount": charge.amount,
                "currency": "USD",
                "reason": "requested_by_customer",
                "stripe_created_at": datetime.combine(
                    charge.charge_date + timedelta(days=3),
                    datetime.min.time(), tzinfo=timezone.utc,
                ),
                "status": "PROCESSED",
            },
        )
        if created:
            self.stdout.write(f"  Created refund for charge {charge.stripe_charge_id}: {charge.amount}")
        return refund

    def _create_payouts(self, company, account, charges):
        today = date.today()
        charges_by_date = sorted(charges, key=lambda c: c.charge_date)

        # Group into biweekly buckets (Stripe pays out every 2 days by default, but weekly for demo)
        buckets = {}
        for charge in charges_by_date:
            payout_date = charge.charge_date + timedelta(days=2)
            # Round to nearest Wednesday
            days_until_wed = (2 - payout_date.weekday()) % 7
            payout_wed = payout_date + timedelta(days=days_until_wed)
            bucket_key = payout_wed.isoformat()
            buckets.setdefault(bucket_key, []).append(charge)

        payout_count = 0
        for idx, (payout_date_str, bucket_charges) in enumerate(sorted(buckets.items())):
            payout_date_val = date.fromisoformat(payout_date_str)

            gross = sum(c.amount for c in bucket_charges)
            fees = sum(c.fee for c in bucket_charges)
            net = gross - fees

            payout, created = StripePayout.objects.get_or_create(
                company=company,
                stripe_payout_id=f"po_demo_{idx:04d}",
                defaults={
                    "account": account,
                    "gross_amount": gross,
                    "fees": fees,
                    "net_amount": net,
                    "currency": "USD",
                    "stripe_status": "paid",
                    "payout_date": payout_date_val,
                    "status": "PROCESSED",
                },
            )

            if not created:
                continue

            payout_count += 1

            for txn_idx, charge in enumerate(bucket_charges):
                is_last_payout = idx == len(buckets) - 1
                verified = not is_last_payout or txn_idx < len(bucket_charges) // 2

                StripePayoutTransaction.objects.get_or_create(
                    company=company,
                    stripe_balance_txn_id=f"txn_demo_{idx:04d}_{txn_idx:03d}",
                    defaults={
                        "payout": payout,
                        "transaction_type": "charge",
                        "amount": charge.amount,
                        "fee": charge.fee,
                        "net": charge.net,
                        "currency": "USD",
                        "source_id": charge.stripe_charge_id,
                        "verified": verified,
                        "local_charge": charge if verified else None,
                        "processed_at": charge.stripe_created_at,
                    },
                )

        self.stdout.write(f"  Created {payout_count} payouts with transactions")
