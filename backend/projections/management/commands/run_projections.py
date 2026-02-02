# projections/management/commands/run_projections.py
"""
Management command to run projections.

Usage:
    # Process pending events for all companies, all projections
    python manage.py run_projections

    # Process specific projection
    python manage.py run_projections --projection account_balance

    # Process specific company
    python manage.py run_projections --company my-company-slug

    # Rebuild projection from scratch
    python manage.py run_projections --projection account_balance --rebuild

    # Verify projection integrity
    python manage.py run_projections --projection account_balance --verify

    # Verify event integrity before rebuild (Ledger Survivability)
    python manage.py run_projections --rebuild --verify-integrity --strict

    # Dry run: verify integrity without rebuilding
    python manage.py run_projections --verify-integrity --dry-run

    # Output diagnostics to file
    python manage.py run_projections --verify-integrity --diagnostics report.json

    # Run continuously (daemon mode)
    python manage.py run_projections --daemon --interval 5
"""

import json
import time
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import Company
from projections.base import projection_registry


class Command(BaseCommand):
    """Run projections to process pending events."""
    
    help = "Process pending events through projections"

    def add_arguments(self, parser):
        parser.add_argument(
            "--projection",
            type=str,
            help="Specific projection to run (default: all)",
        )
        parser.add_argument(
            "--company",
            type=str,
            help="Company slug to process (default: all active companies)",
        )
        parser.add_argument(
            "--rebuild",
            action="store_true",
            help="Rebuild projection from scratch (clears existing data)",
        )
        parser.add_argument(
            "--verify",
            action="store_true",
            help="Verify projection integrity without processing",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=1000,
            help="Maximum events to process per run (default: 1000)",
        )
        parser.add_argument(
            "--daemon",
            action="store_true",
            help="Run continuously, processing events as they arrive",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=5,
            help="Seconds between daemon runs (default: 5)",
        )
        # Ledger Survivability: Integrity verification options
        parser.add_argument(
            "--verify-integrity",
            action="store_true",
            help="Verify event integrity (payload hashes, sequence continuity)",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Abort on ANY integrity violation (hard-fail mode)",
        )
        parser.add_argument(
            "--diagnostics",
            type=str,
            metavar="FILE",
            help="Output diagnostics to JSON file",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Verify integrity without rebuilding projections",
        )

    def handle(self, *args, **options):
        # Get projections to run
        projections = self._get_projections(options.get("projection"))
        
        # Get companies to process
        companies = self._get_companies(options.get("company"))
        
        if not companies:
            self.stdout.write(self.style.WARNING("No active companies found."))
            return

        # Ledger Survivability: Integrity verification
        if options.get("verify_integrity"):
            self._verify_event_integrity(companies, options)
            if options.get("dry_run"):
                return
            # Continue to rebuild if --rebuild is also specified

        if options.get("verify"):
            self._verify_projections(projections, companies)
            return

        if options.get("rebuild"):
            self._rebuild_projections(projections, companies)
            return
        
        if options.get("daemon"):
            self._run_daemon(
                projections,
                companies,
                options.get("limit"),
                options.get("interval"),
            )
        else:
            self._run_once(projections, companies, options.get("limit"))
    
    def _get_projections(self, name=None):
        """Get projections to run."""
        if name:
            projection = projection_registry.get(name)
            if not projection:
                available = ", ".join(projection_registry.names())
                raise CommandError(
                    f"Unknown projection: {name}. Available: {available}"
                )
            return [projection]
        
        projections = projection_registry.all()
        if not projections:
            raise CommandError("No projections registered.")
        
        return projections
    
    def _get_companies(self, slug=None):
        """Get companies to process."""
        if slug:
            try:
                return [Company.objects.get(slug=slug, is_active=True)]
            except Company.DoesNotExist:
                raise CommandError(f"Company not found or inactive: {slug}")
        
        return list(Company.objects.filter(is_active=True))
    
    def _run_once(self, projections, companies, limit):
        """Run projections once."""
        total_processed = 0
        
        for company in companies:
            self.stdout.write(f"\nProcessing company: {company.name}")
            
            for projection in projections:
                self.stdout.write(f"  Running projection: {projection.name}")
                
                processed = projection.process_pending(
                    company=company,
                    limit=limit,
                )
                
                total_processed += processed
                
                if processed > 0:
                    self.stdout.write(
                        self.style.SUCCESS(f"    Processed {processed} events")
                    )
                else:
                    self.stdout.write(f"    No pending events")
        
        self.stdout.write(
            self.style.SUCCESS(f"\nTotal events processed: {total_processed}")
        )
    
    def _run_daemon(self, projections, companies, limit, interval):
        """Run projections continuously."""
        self.stdout.write(
            f"Starting daemon mode (interval: {interval}s, Ctrl+C to stop)"
        )
        
        try:
            while True:
                total_processed = 0
                
                for company in companies:
                    for projection in projections:
                        processed = projection.process_pending(
                            company=company,
                            limit=limit,
                        )
                        total_processed += processed
                
                if total_processed > 0:
                    self.stdout.write(f"Processed {total_processed} events")
                
                time.sleep(interval)
                
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\nDaemon stopped."))
    
    def _rebuild_projections(self, projections, companies):
        """Rebuild projections from scratch."""
        self.stdout.write(self.style.WARNING("REBUILDING PROJECTIONS"))
        self.stdout.write("This will clear all projected data and replay events.\n")
        
        for company in companies:
            self.stdout.write(f"\nRebuilding for company: {company.name}")
            
            for projection in projections:
                self.stdout.write(f"  Rebuilding projection: {projection.name}")
                
                processed = projection.rebuild(company)
                
                self.stdout.write(
                    self.style.SUCCESS(f"    Replayed {processed} events")
                )
        
        self.stdout.write(self.style.SUCCESS("\nRebuild complete."))
    
    def _verify_projections(self, projections, companies):
        """Verify projection integrity."""
        self.stdout.write("VERIFYING PROJECTION INTEGRITY\n")
        
        all_ok = True
        
        for company in companies:
            self.stdout.write(f"\nVerifying company: {company.name}")
            
            for projection in projections:
                self.stdout.write(f"  Projection: {projection.name}")
                
                # Check lag
                lag = projection.get_lag(company)
                if lag > 0:
                    self.stdout.write(
                        self.style.WARNING(f"    Lag: {lag} unprocessed events")
                    )
                    all_ok = False
                else:
                    self.stdout.write(f"    Lag: 0 (caught up)")
                
                # Run verification if available
                if hasattr(projection, "verify_all_balances"):
                    result = projection.verify_all_balances(company)
                    
                    if result["mismatches"]:
                        self.stdout.write(
                            self.style.ERROR(
                                f"    MISMATCHES: {len(result['mismatches'])}"
                            )
                        )
                        for m in result["mismatches"]:
                            self.stdout.write(f"      {m['account_code']}: "
                                f"projected D={m['projected_debit']} C={m['projected_credit']}, "
                                f"expected D={m['expected_debit']} C={m['expected_credit']}")
                        all_ok = False
                    else:
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"    Verified: {result['verified']}/{result['total_accounts']} accounts"
                            )
                        )
        
        if all_ok:
            self.stdout.write(self.style.SUCCESS("\nAll projections verified OK."))
        else:
            self.stdout.write(self.style.ERROR("\nVerification found issues."))

    def _verify_event_integrity(self, companies, options):
        """
        Verify event integrity for Ledger Survivability.

        PRD Section 5.2: Replay Engine Requirements
        - Verify payload hashes
        - Check sequence continuity
        - Hard-fail on any inconsistency
        """
        from events.verification import full_integrity_check
        from events.integrity import IntegrityViolationError

        self.stdout.write(self.style.WARNING("VERIFYING EVENT INTEGRITY"))
        self.stdout.write("Checking payload hashes and sequence continuity...\n")

        diagnostics = {
            'timestamp': timezone.now().isoformat(),
            'companies': {},
            'errors': [],
            'success': True,
        }

        all_valid = True

        for company in companies:
            self.stdout.write(f"\nVerifying company: {company.name}")

            # Full integrity check
            result = full_integrity_check(company, verbose=True)
            diagnostics['companies'][str(company.public_id)] = {
                'name': company.name,
                'slug': company.slug,
                **result,
            }

            # Report results
            self.stdout.write(f"  Total events: {result['total_events']}")
            self.stdout.write(f"  Verified: {result['verified_events']}")
            self.stdout.write(f"  Inline: {result['inline_event_count']}")
            self.stdout.write(f"  External: {result['external_payload_count']}")
            self.stdout.write(f"  Chunked: {result['chunked_event_count']}")
            self.stdout.write(f"  Total payload bytes: {result['total_payload_bytes']:,}")

            if result['payload_errors']:
                all_valid = False
                diagnostics['success'] = False

                self.stdout.write(
                    self.style.ERROR(f"  PAYLOAD ERRORS: {len(result['payload_errors'])}")
                )
                for error in result['payload_errors']:
                    self.stdout.write(
                        self.style.ERROR(
                            f"    [{error['error_type']}] {error['message']}"
                        )
                    )
                    diagnostics['errors'].append(error)

            if result['sequence_gaps']:
                all_valid = False
                diagnostics['success'] = False

                self.stdout.write(
                    self.style.ERROR(f"  SEQUENCE GAPS: {len(result['sequence_gaps'])}")
                )
                for gap in result['sequence_gaps']:
                    self.stdout.write(
                        self.style.ERROR(
                            f"    Gap: {gap['start']}-{gap['end']} "
                            f"({gap['missing_count']} events missing)"
                        )
                    )

            if result['is_valid']:
                self.stdout.write(
                    self.style.SUCCESS(f"  PASSED: All events verified")
                )

        # Write diagnostics file
        if options.get('diagnostics'):
            diagnostics_path = options['diagnostics']
            with open(diagnostics_path, 'w') as f:
                json.dump(diagnostics, f, indent=2, default=str)
            self.stdout.write(f"\nDiagnostics written to: {diagnostics_path}")

        # Handle strict mode
        if options.get('strict') and not all_valid:
            self.stdout.write(
                self.style.ERROR(
                    "\nABORTED: Integrity violations detected in strict mode."
                )
            )
            self.stdout.write(
                self.style.ERROR(
                    "System should be marked UNSAFE until issues are resolved."
                )
            )
            raise CommandError("Integrity verification failed in strict mode")

        # Summary
        if all_valid:
            self.stdout.write(self.style.SUCCESS("\nEvent integrity verified OK."))
        else:
            self.stdout.write(
                self.style.ERROR("\nIntegrity verification found issues.")
            )