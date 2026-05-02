Nxentra test CSV pack
Generated: 2026-05-02

Purpose:
This pack tests Shopify -> provider clearing -> settlement -> bank reconciliation.

Files:
1. shopify_orders_test.csv
2. paymob_settlements_test.csv
3. bosta_cod_settlements_test.csv
4. bank_statement_test.csv

Built-in test scenarios:
- Order 1001: Paymob happy path.
- Order 1002: Shopify paid but no Paymob settlement yet; should remain unsettled.
- Order 1003: Paymob settlement with higher fee; test fee/difference handling.
- Order 1004: Partial refund deducted in Paymob settlement.
- Order 1005: Bosta COD happy path.
- Order 1006: Bosta COD expected 2,050 but bank receives 1,850; short by 200.
- Order 1007: COD failed delivery / returned.
- Order 1008: Paymob happy path with discount and shipping.
- Order 1009: Gateway alias normalization: "Paymob Accept".
- Order 1010: Bosta settlement expected but missing bank deposit.
- Paymob order 9999: settlement row without Shopify order.
- Bosta order 8888: courier row without Shopify order.
- Bank UNKNOWN-TRF-001: unmatched bank deposit.

Expected high-level outcomes:
- Paymob batch PAYMOB-BATCH-APR30-A should match bank credit 2,520.
- Paymob batch PAYMOB-BATCH-MAY01-A should match bank credit 276.
- Paymob batch PAYMOB-BATCH-MAY01-B should match bank credit 3,880.
- Bosta BST-700 should match bank credit 1,400.
- Bosta BST-701 should show short payment of 200.
- Bosta BST-702 should show missing bank deposit.
- Shopify order 1002 should show unsettled / still in Paymob clearing.
