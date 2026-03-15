import { useState, useEffect } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import {
  Building2,
  Upload,
  CheckCircle2,
  Clock,
  FileSpreadsheet,
  Loader2,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import {
  bankReconciliationService,
  BankStatementSummary,
} from "@/services/bank-reconciliation.service";

const STATUS_BADGE: Record<string, { label: string; className: string }> = {
  IMPORTED: { label: "Imported", className: "bg-blue-100 text-blue-700" },
  IN_PROGRESS: { label: "In Progress", className: "bg-yellow-100 text-yellow-700" },
  RECONCILED: { label: "Reconciled", className: "bg-green-100 text-green-700" },
};

export default function BankReconciliationPage() {
  const router = useRouter();
  const { toast } = useToast();
  const [statements, setStatements] = useState<BankStatementSummary[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchStatements = async () => {
    setLoading(true);
    try {
      const { data } = await bankReconciliationService.getStatements();
      setStatements(data);
    } catch {
      toast({ title: "Failed to load statements.", variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStatements();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Bank Reconciliation"
          subtitle="Import bank statements and reconcile against journal entries"
          actions={
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={() => router.push("/accounting/bank-reconciliation/commerce")}
              >
                <FileSpreadsheet className="me-2 h-4 w-4" />
                Commerce View
              </Button>
              <Button onClick={() => router.push("/accounting/bank-reconciliation/import")}>
                <Upload className="me-2 h-4 w-4" />
                Import Statement
              </Button>
            </div>
          }
        />

        {loading ? (
          <Card>
            <CardContent className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </CardContent>
          </Card>
        ) : statements.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center justify-center py-12 gap-3">
              <Building2 className="h-10 w-10 text-muted-foreground" />
              <p className="text-muted-foreground">No bank statements imported yet.</p>
              <Button
                variant="outline"
                onClick={() => router.push("/accounting/bank-reconciliation/import")}
              >
                <Upload className="me-2 h-4 w-4" />
                Import Your First Statement
              </Button>
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <FileSpreadsheet className="h-5 w-5" />
                Bank Statements
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 font-medium">Account</th>
                      <th className="pb-2 font-medium">Statement Date</th>
                      <th className="pb-2 font-medium">Period</th>
                      <th className="pb-2 font-medium text-right">Closing Balance</th>
                      <th className="pb-2 font-medium text-center">Lines</th>
                      <th className="pb-2 font-medium text-center">Matched</th>
                      <th className="pb-2 font-medium text-center">Status</th>
                      <th className="pb-2 font-medium"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {statements.map((s) => {
                      const badge = STATUS_BADGE[s.status] || STATUS_BADGE.IMPORTED;
                      return (
                        <tr
                          key={s.id}
                          className="border-b last:border-0 hover:bg-muted/50 cursor-pointer"
                          onClick={() =>
                            router.push(`/accounting/bank-reconciliation/${s.id}`)
                          }
                        >
                          <td className="py-3">
                            <div className="font-medium">{s.account_code}</div>
                            <div className="text-xs text-muted-foreground">
                              {s.account_name}
                            </div>
                          </td>
                          <td className="py-3">{s.statement_date}</td>
                          <td className="py-3 text-muted-foreground">
                            {s.period_start} to {s.period_end}
                          </td>
                          <td className="py-3 text-right font-mono">
                            {s.currency} {Number(s.closing_balance).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                          </td>
                          <td className="py-3 text-center">{s.line_count}</td>
                          <td className="py-3 text-center">
                            {s.matched_count}/{s.line_count}
                          </td>
                          <td className="py-3 text-center">
                            <span
                              className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${badge.className}`}
                            >
                              {s.status === "RECONCILED" ? (
                                <CheckCircle2 className="h-3 w-3" />
                              ) : (
                                <Clock className="h-3 w-3" />
                              )}
                              {badge.label}
                            </span>
                          </td>
                          <td className="py-3 text-right">
                            <Button variant="ghost" size="sm">
                              View
                            </Button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => ({
  props: {
    ...(await serverSideTranslations(locale ?? "en", ["common"])),
  },
});
