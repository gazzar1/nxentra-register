import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { useState } from "react";
import { Plus, Search, Receipt, DollarSign } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import {
  useExpenses,
  useCreateExpense,
  useProperties,
} from "@/queries/useProperties";
import { useToast } from "@/components/ui/toaster";
import { cn } from "@/lib/cn";
import type { ExpenseCategory, ExpensePaymentMode } from "@/types/properties";

const CATEGORY_LABELS: Record<string, string> = {
  maintenance: "Maintenance",
  utilities: "Utilities",
  cleaning: "Cleaning",
  security: "Security",
  salary: "Salary",
  tax: "Tax",
  insurance: "Insurance",
  legal: "Legal",
  marketing: "Marketing",
  other: "Other",
};

const PAYMENT_MODE_LABELS: Record<string, string> = {
  cash_paid: "Cash Paid",
  credit: "Credit",
};

const PAID_STATUS_COLORS: Record<string, string> = {
  unpaid: "bg-red-100 text-red-800",
  paid: "bg-green-100 text-green-800",
  partially_paid: "bg-orange-100 text-orange-800",
};

export default function ExpensesPage() {
  const { data: expenses, isLoading } = useExpenses();
  const { data: properties } = useProperties();
  const createExpense = useCreateExpense();
  const { toast } = useToast();
  const [search, setSearch] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({
    property_id: "",
    category: "maintenance" as ExpenseCategory,
    expense_date: "",
    amount: "",
    payment_mode: "cash_paid" as ExpensePaymentMode,
    vendor_ref: "",
    description: "",
  });

  const filtered = expenses?.filter((e) => {
    if (!search) return true;
    const s = search.toLowerCase();
    return (
      e.property_code.toLowerCase().includes(s) ||
      e.property_name.toLowerCase().includes(s) ||
      e.category.toLowerCase().includes(s) ||
      (e.vendor_ref || "").toLowerCase().includes(s) ||
      (e.description || "").toLowerCase().includes(s)
    );
  });

  const totalExpenses =
    expenses?.reduce((sum, e) => sum + Number(e.amount), 0) ?? 0;

  const handleCreate = async () => {
    try {
      await createExpense.mutateAsync({
        property_id: Number(form.property_id),
        category: form.category,
        expense_date: form.expense_date,
        amount: Number(form.amount),
        payment_mode: form.payment_mode,
        vendor_ref: form.vendor_ref || null,
        description: form.description || null,
      });
      setCreateOpen(false);
      setForm({
        property_id: "",
        category: "maintenance",
        expense_date: "",
        amount: "",
        payment_mode: "cash_paid",
        vendor_ref: "",
        description: "",
      });
      toast({ title: "Expense recorded" });
    } catch (err: any) {
      toast({
        title: "Failed to record expense",
        description: err?.response?.data?.detail || "Could not record expense.",
        variant: "destructive",
      });
    }
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Expenses"
          subtitle="Property expense tracking"
          actions={
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="mr-2 h-4 w-4" />
              Record Expense
            </Button>
          }
        />

        {expenses && expenses.length > 0 && (
          <Card>
            <CardContent className="pt-4">
              <div className="text-sm text-muted-foreground">
                Total Expenses
              </div>
              <div className="text-xl font-bold">
                {totalExpenses.toLocaleString()} SAR
              </div>
            </CardContent>
          </Card>
        )}

        <div className="flex items-center gap-4">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Search expenses..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9"
            />
          </div>
        </div>

        {isLoading ? (
          <LoadingSpinner />
        ) : !filtered?.length ? (
          <EmptyState
            icon={<Receipt className="h-12 w-12" />}
            title="No expenses found"
            description="Record your first property expense."
            action={
              <Button onClick={() => setCreateOpen(true)}>
                <Plus className="mr-2 h-4 w-4" />
                Record Expense
              </Button>
            }
          />
        ) : (
          <div className="rounded-lg border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="px-4 py-3 text-left font-medium">Date</th>
                  <th className="px-4 py-3 text-left font-medium">Property</th>
                  <th className="px-4 py-3 text-left font-medium">Category</th>
                  <th className="px-4 py-3 text-left font-medium">Vendor</th>
                  <th className="px-4 py-3 text-right font-medium">Amount</th>
                  <th className="px-4 py-3 text-center font-medium">Mode</th>
                  <th className="px-4 py-3 text-center font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((expense) => (
                  <tr key={expense.id} className="border-b">
                    <td className="px-4 py-3">{expense.expense_date}</td>
                    <td className="px-4 py-3">
                      {expense.property_code} - {expense.property_name}
                    </td>
                    <td className="px-4 py-3">
                      {CATEGORY_LABELS[expense.category] || expense.category}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {expense.vendor_ref || "—"}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {Number(expense.amount).toLocaleString()}{" "}
                      {expense.currency}
                    </td>
                    <td className="px-4 py-3 text-center text-muted-foreground">
                      {PAYMENT_MODE_LABELS[expense.payment_mode] ||
                        expense.payment_mode}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <Badge
                        className={cn(
                          "text-xs",
                          PAID_STATUS_COLORS[expense.paid_status]
                        )}
                      >
                        {expense.paid_status}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Create Expense Dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Record Expense</DialogTitle>
            <DialogDescription>
              Record a property expense transaction.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="expense_property">Property</Label>
              <Select
                value={form.property_id}
                onValueChange={(v) => setForm({ ...form, property_id: v })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select property" />
                </SelectTrigger>
                <SelectContent>
                  {properties?.map((p) => (
                    <SelectItem key={p.id} value={String(p.id)}>
                      {p.code} - {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="expense_category">Category</Label>
                <Select
                  value={form.category}
                  onValueChange={(v) =>
                    setForm({ ...form, category: v as ExpenseCategory })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(CATEGORY_LABELS).map(([val, label]) => (
                      <SelectItem key={val} value={val}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="expense_payment_mode">Payment Mode</Label>
                <Select
                  value={form.payment_mode}
                  onValueChange={(v) =>
                    setForm({
                      ...form,
                      payment_mode: v as ExpensePaymentMode,
                    })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="cash_paid">Cash Paid</SelectItem>
                    <SelectItem value="credit">Credit</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="expense_amount">Amount</Label>
                <Input
                  id="expense_amount"
                  type="number"
                  value={form.amount}
                  onChange={(e) => setForm({ ...form, amount: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="expense_date">Date</Label>
                <Input
                  id="expense_date"
                  type="date"
                  value={form.expense_date}
                  onChange={(e) =>
                    setForm({ ...form, expense_date: e.target.value })
                  }
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="expense_vendor">Vendor Reference (optional)</Label>
              <Input
                id="expense_vendor"
                value={form.vendor_ref}
                onChange={(e) =>
                  setForm({ ...form, vendor_ref: e.target.value })
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="expense_description">
                Description (optional)
              </Label>
              <Input
                id="expense_description"
                value={form.description}
                onChange={(e) =>
                  setForm({ ...form, description: e.target.value })
                }
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleCreate}
              disabled={
                !form.property_id ||
                !form.amount ||
                !form.expense_date ||
                createExpense.isPending
              }
            >
              {createExpense.isPending ? "Saving..." : "Record Expense"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
