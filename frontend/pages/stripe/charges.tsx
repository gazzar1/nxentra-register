import { useState, useEffect, useMemo } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import {
  CreditCard,
  Search,
  CheckCircle2,
  XCircle,
  Clock,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader, EmptyState } from "@/components/common";
import { stripeService, StripeChargeItem } from "@/services/stripe.service";

function fmt(amount: string | number, currency = "USD") {
  const n = typeof amount === "string" ? parseFloat(amount) : amount;
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
  }).format(n);
}

export default function StripeChargesPage() {
  const [charges, setCharges] = useState<StripeChargeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  const fetchCharges = async () => {
    setLoading(true);
    try {
      const { data } = await stripeService.getCharges();
      setCharges(data);
    } catch {
      // handled by api client
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCharges();
  }, []);

  const filtered = useMemo(() => {
    if (!search.trim()) return charges;
    const q = search.toLowerCase();
    return charges.filter(
      (c) =>
        c.stripe_charge_id.toLowerCase().includes(q) ||
        c.description.toLowerCase().includes(q) ||
        c.customer_name.toLowerCase().includes(q) ||
        c.customer_email.toLowerCase().includes(q)
    );
  }, [charges, search]);

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Stripe Charges"
          subtitle="All charges processed through Stripe"
          actions={
            <Button variant="outline" size="sm" onClick={fetchCharges} disabled={loading}>
              <RefreshCw className={`me-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
          }
        />

        <Card>
          <CardContent className="pt-6">
            <div className="mb-4">
              <div className="relative max-w-sm">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Search charges..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="pl-9"
                />
              </div>
            </div>

            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : filtered.length === 0 ? (
              <EmptyState
                icon={<CreditCard className="h-12 w-12" />}
                title={search ? "No matching charges" : "No charges yet"}
                description={
                  search
                    ? "Try a different search term."
                    : "Charges will appear here when payments are processed through Stripe."
                }
              />
            ) : (
              <div className="rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Charge ID</TableHead>
                      <TableHead>Date</TableHead>
                      <TableHead>Description</TableHead>
                      <TableHead>Customer</TableHead>
                      <TableHead className="text-right">Amount</TableHead>
                      <TableHead className="text-right">Fee</TableHead>
                      <TableHead className="text-right">Net</TableHead>
                      <TableHead>Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filtered.map((c) => (
                      <TableRow key={c.id}>
                        <TableCell className="font-mono text-xs">{c.stripe_charge_id}</TableCell>
                        <TableCell>{new Date(c.charge_date).toLocaleDateString()}</TableCell>
                        <TableCell className="max-w-[200px] truncate">{c.description || "\u2014"}</TableCell>
                        <TableCell className="text-sm">
                          {c.customer_name || c.customer_email || "\u2014"}
                        </TableCell>
                        <TableCell className="text-right font-mono">{fmt(c.amount, c.currency)}</TableCell>
                        <TableCell className="text-right font-mono text-muted-foreground">{fmt(c.fee, c.currency)}</TableCell>
                        <TableCell className="text-right font-mono">{fmt(c.net, c.currency)}</TableCell>
                        <TableCell>
                          {c.status === "PROCESSED" ? (
                            <Badge variant="success"><CheckCircle2 className="me-1 h-3 w-3" />Processed</Badge>
                          ) : c.status === "ERROR" ? (
                            <Badge variant="destructive"><XCircle className="me-1 h-3 w-3" />Error</Badge>
                          ) : (
                            <Badge variant="secondary"><Clock className="me-1 h-3 w-3" />Received</Badge>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
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
