import { useState, useEffect } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import {
  ArrowLeftRight,
  Search,
  Loader2,
  ArrowDownLeft,
  ArrowUpRight,
  Ban,
  Unlink,
  CheckCircle2,
  XCircle,
  Filter,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import {
  bankService,
  BankAccount,
  BankTransaction,
} from "@/services/bank.service";

export default function BankTransactionsPage() {
  const router = useRouter();
  const { toast } = useToast();

  const [accounts, setAccounts] = useState<BankAccount[]>([]);
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const [transactions, setTransactions] = useState<BankTransaction[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const limit = 50;

  useEffect(() => {
    bankService.getAccounts().then(({ data }) => {
      setAccounts(data);
      const qId = Number(router.query.account);
      if (qId && data.find((a) => a.id === qId)) {
        setSelectedAccountId(qId);
      }
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    loadTransactions();
  }, [selectedAccountId, statusFilter, typeFilter, offset]); // eslint-disable-line react-hooks/exhaustive-deps

  async function loadTransactions() {
    setLoading(true);
    try {
      const { data } = await bankService.getTransactions({
        bank_account_id: selectedAccountId || undefined,
        status: statusFilter || undefined,
        type: typeFilter || undefined,
        search: search || undefined,
        limit,
        offset,
      });
      setTransactions(data.results);
      setTotal(data.total);
    } finally {
      setLoading(false);
    }
  }

  function handleSearch() {
    setOffset(0);
    loadTransactions();
  }

  async function handleAction(txId: number, action: string) {
    try {
      await bankService.updateTransaction(txId, { action });
      toast({ title: `Transaction ${action}d.` });
      loadTransactions();
    } catch {
      toast({ title: "Action failed.", variant: "destructive" });
    }
  }

  const statusBadge = (status: string) => {
    switch (status) {
      case "MATCHED":
        return (
          <Badge variant="success" className="gap-1">
            <CheckCircle2 className="h-3 w-3" /> Matched
          </Badge>
        );
      case "EXCLUDED":
        return (
          <Badge variant="secondary" className="gap-1">
            <Ban className="h-3 w-3" /> Excluded
          </Badge>
        );
      default:
        return (
          <Badge variant="warning" className="gap-1">
            <XCircle className="h-3 w-3" /> Unmatched
          </Badge>
        );
    }
  };

  const totalPages = Math.ceil(total / limit);
  const currentPage = Math.floor(offset / limit) + 1;

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Bank Transactions"
          subtitle="View and manage imported bank transactions"
        />

        {/* Filters */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex flex-wrap gap-3 items-end">
              <div className="space-y-1 min-w-[200px]">
                <label className="text-xs font-medium text-muted-foreground">
                  Bank Account
                </label>
                <select
                  className="w-full border rounded-md px-3 py-2 text-sm"
                  value={selectedAccountId ?? ""}
                  onChange={(e) => {
                    setSelectedAccountId(
                      e.target.value ? Number(e.target.value) : null
                    );
                    setOffset(0);
                  }}
                >
                  <option value="">All Accounts</option>
                  {accounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.account_name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-1 min-w-[140px]">
                <label className="text-xs font-medium text-muted-foreground">
                  Status
                </label>
                <select
                  className="w-full border rounded-md px-3 py-2 text-sm"
                  value={statusFilter}
                  onChange={(e) => {
                    setStatusFilter(e.target.value);
                    setOffset(0);
                  }}
                >
                  <option value="">All</option>
                  <option value="UNMATCHED">Unmatched</option>
                  <option value="MATCHED">Matched</option>
                  <option value="EXCLUDED">Excluded</option>
                </select>
              </div>

              <div className="space-y-1 min-w-[140px]">
                <label className="text-xs font-medium text-muted-foreground">
                  Type
                </label>
                <select
                  className="w-full border rounded-md px-3 py-2 text-sm"
                  value={typeFilter}
                  onChange={(e) => {
                    setTypeFilter(e.target.value);
                    setOffset(0);
                  }}
                >
                  <option value="">All</option>
                  <option value="CREDIT">Credits Only</option>
                  <option value="DEBIT">Debits Only</option>
                </select>
              </div>

              <div className="flex-1 min-w-[200px]">
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    className="pl-9"
                    placeholder="Search description or reference..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                  />
                </div>
              </div>

              <Button variant="outline" onClick={handleSearch}>
                <Filter className="me-2 h-4 w-4" />
                Apply
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Transactions Table */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <ArrowLeftRight className="h-5 w-5" />
              Transactions
            </CardTitle>
            <span className="text-sm text-muted-foreground">{total} total</span>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : transactions.length === 0 ? (
              <p className="text-sm text-muted-foreground py-8 text-center">
                No transactions found. Import a bank statement to get started.
              </p>
            ) : (
              <>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b">
                        <th className="text-left px-3 py-2 font-medium text-muted-foreground">
                          Date
                        </th>
                        <th className="text-left px-3 py-2 font-medium text-muted-foreground">
                          Description
                        </th>
                        <th className="text-left px-3 py-2 font-medium text-muted-foreground">
                          Reference
                        </th>
                        <th className="text-right px-3 py-2 font-medium text-muted-foreground">
                          Amount
                        </th>
                        <th className="text-right px-3 py-2 font-medium text-muted-foreground">
                          Balance
                        </th>
                        <th className="text-center px-3 py-2 font-medium text-muted-foreground">
                          Status
                        </th>
                        <th className="text-center px-3 py-2 font-medium text-muted-foreground">
                          Actions
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {transactions.map((tx) => {
                        const amt = Number(tx.amount);
                        return (
                          <tr key={tx.id} className="border-b hover:bg-muted/50">
                            <td className="px-3 py-2.5 whitespace-nowrap font-mono text-xs">
                              {tx.transaction_date}
                            </td>
                            <td className="px-3 py-2.5 max-w-[300px] truncate">
                              {tx.description}
                            </td>
                            <td className="px-3 py-2.5 text-muted-foreground text-xs font-mono">
                              {tx.reference || "—"}
                            </td>
                            <td className="px-3 py-2.5 text-right whitespace-nowrap font-mono font-medium">
                              <span
                                className={
                                  amt >= 0 ? "text-green-600" : "text-red-600"
                                }
                              >
                                {amt >= 0 ? (
                                  <ArrowDownLeft className="inline h-3 w-3 me-1" />
                                ) : (
                                  <ArrowUpRight className="inline h-3 w-3 me-1" />
                                )}
                                {Math.abs(amt).toLocaleString(undefined, {
                                  minimumFractionDigits: 2,
                                })}
                              </span>
                            </td>
                            <td className="px-3 py-2.5 text-right font-mono text-xs text-muted-foreground">
                              {tx.running_balance
                                ? Number(tx.running_balance).toLocaleString(
                                    undefined,
                                    { minimumFractionDigits: 2 }
                                  )
                                : "—"}
                            </td>
                            <td className="px-3 py-2.5 text-center">
                              {statusBadge(tx.status)}
                            </td>
                            <td className="px-3 py-2.5 text-center">
                              {tx.status === "UNMATCHED" && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => handleAction(tx.id, "exclude")}
                                  title="Exclude from reconciliation"
                                >
                                  <Ban className="h-3.5 w-3.5" />
                                </Button>
                              )}
                              {tx.status === "MATCHED" && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => handleAction(tx.id, "unmatch")}
                                  title="Unmatch"
                                >
                                  <Unlink className="h-3.5 w-3.5" />
                                </Button>
                              )}
                              {tx.status === "EXCLUDED" && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => handleAction(tx.id, "unmatch")}
                                  title="Restore"
                                >
                                  <ArrowLeftRight className="h-3.5 w-3.5" />
                                </Button>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                {/* Pagination */}
                {totalPages > 1 && (
                  <div className="flex items-center justify-between mt-4 pt-4 border-t">
                    <p className="text-sm text-muted-foreground">
                      Page {currentPage} of {totalPages}
                    </p>
                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={offset === 0}
                        onClick={() => setOffset(Math.max(0, offset - limit))}
                      >
                        Previous
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={offset + limit >= total}
                        onClick={() => setOffset(offset + limit)}
                      >
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
