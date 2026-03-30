import { useState, useEffect } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import Link from "next/link";
import {
  Banknote,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Clock,
  Loader2,
  RefreshCw,
  ArrowRight,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader, EmptyState } from "@/components/common";
import { stripeService, StripePayoutListItem } from "@/services/stripe.service";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";

function reconBadge(status: StripePayoutListItem["reconciliation_status"]) {
  switch (status) {
    case "verified":
      return <Badge variant="success"><CheckCircle2 className="me-1 h-3 w-3" />Matched</Badge>;
    case "partial":
      return <Badge variant="warning"><AlertTriangle className="me-1 h-3 w-3" />Partial</Badge>;
    case "discrepancy":
      return <Badge variant="destructive"><XCircle className="me-1 h-3 w-3" />Mismatch</Badge>;
    default:
      return <Badge variant="secondary"><Clock className="me-1 h-3 w-3" />Unverified</Badge>;
  }
}

export default function StripePayoutsPage() {
  const { formatCurrency, formatAmount, formatDate } = useCompanyFormat();
  const [payouts, setPayouts] = useState<StripePayoutListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);

  const fetchPayouts = async (p = 1) => {
    setLoading(true);
    try {
      const { data } = await stripeService.getPayouts(p);
      setPayouts(data.results);
      setTotal(data.total);
      setPage(data.page);
    } catch {
      // handled by api client
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPayouts();
  }, []);

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Stripe Payouts"
          subtitle="Payout history from Stripe"
          actions={
            <div className="flex gap-2">
              <Link href="/stripe/reconciliation">
                <Button variant="outline" size="sm">
                  Reconciliation
                  <ArrowRight className="ms-2 h-4 w-4" />
                </Button>
              </Link>
              <Button variant="outline" size="sm" onClick={() => fetchPayouts(page)} disabled={loading}>
                <RefreshCw className={`me-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                Refresh
              </Button>
            </div>
          }
        />

        <Card>
          <CardContent className="pt-6">
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : payouts.length === 0 ? (
              <EmptyState
                icon={<Banknote className="h-12 w-12" />}
                title="No payouts yet"
                description="Payouts will appear here once Stripe sends funds to your bank account."
              />
            ) : (
              <>
                <div className="rounded-md border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Payout ID</TableHead>
                        <TableHead>Date</TableHead>
                        <TableHead className="text-right">Gross</TableHead>
                        <TableHead className="text-right">Fees</TableHead>
                        <TableHead className="text-right">Net</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Reconciliation</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {payouts.map((p) => (
                        <TableRow key={p.stripe_payout_id}>
                          <TableCell className="font-mono text-xs">{p.stripe_payout_id}</TableCell>
                          <TableCell>
                            {formatDate(p.payout_date)}
                          </TableCell>
                          <TableCell className="text-right font-mono">{formatCurrency(p.gross_amount, p.currency)}</TableCell>
                          <TableCell className="text-right font-mono text-muted-foreground">{formatCurrency(p.fees, p.currency)}</TableCell>
                          <TableCell className="text-right font-mono font-medium">{formatCurrency(p.net_amount, p.currency)}</TableCell>
                          <TableCell>
                            <Badge variant="outline" className="capitalize">{p.stripe_status}</Badge>
                          </TableCell>
                          <TableCell>
                            {reconBadge(p.reconciliation_status)}
                            {p.transactions_total > 0 && (
                              <span className="ms-2 text-xs text-muted-foreground">
                                {p.transactions_verified}/{p.transactions_total}
                              </span>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>

                {total > 25 && (
                  <div className="flex items-center justify-between mt-4">
                    <p className="text-sm text-muted-foreground">
                      Showing {payouts.length} of {total} payouts
                    </p>
                    <div className="flex gap-2">
                      <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => fetchPayouts(page - 1)}>
                        Previous
                      </Button>
                      <Button variant="outline" size="sm" disabled={page * 25 >= total} onClick={() => fetchPayouts(page + 1)}>
                        Next
                      </Button>
                    </div>
                  </div>
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
