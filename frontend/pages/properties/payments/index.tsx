import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { Search, DollarSign } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import { usePayments } from "@/queries/useProperties";
import { cn } from "@/lib/cn";

const ALLOCATION_STATUS_COLORS: Record<string, string> = {
  unallocated: "bg-gray-100 text-gray-800",
  partially_allocated: "bg-orange-100 text-orange-800",
  fully_allocated: "bg-green-100 text-green-800",
};

export default function PaymentsPage() {
  const router = useRouter();
  const { data: payments, isLoading } = usePayments();
  const [search, setSearch] = useState("");

  const filtered = payments?.filter((p) => {
    if (!search) return true;
    const s = search.toLowerCase();
    return (
      p.receipt_no.toLowerCase().includes(s) ||
      p.lessee_name.toLowerCase().includes(s) ||
      p.lease_contract_no.toLowerCase().includes(s)
    );
  });

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Payments"
          subtitle="Rent payment receipts"
        />

        <div className="flex items-center gap-4">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Search payments..."
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
            icon={<DollarSign className="h-12 w-12" />}
            title="No payments found"
            description="Payments are created from the lease context."
          />
        ) : (
          <div className="rounded-lg border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="px-4 py-3 text-left font-medium">Receipt #</th>
                  <th className="px-4 py-3 text-left font-medium">Lease</th>
                  <th className="px-4 py-3 text-left font-medium">Lessee</th>
                  <th className="px-4 py-3 text-left font-medium">Date</th>
                  <th className="px-4 py-3 text-right font-medium">Amount</th>
                  <th className="px-4 py-3 text-left font-medium">Method</th>
                  <th className="px-4 py-3 text-center font-medium">Allocation Status</th>
                  <th className="px-4 py-3 text-center font-medium">Voided</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((payment) => (
                  <tr
                    key={payment.id}
                    className="border-b hover:bg-muted/30 cursor-pointer"
                    onClick={() => router.push(`/properties/leases/${payment.lease}`)}
                  >
                    <td className="px-4 py-3 font-medium">{payment.receipt_no}</td>
                    <td className="px-4 py-3">{payment.lease_contract_no}</td>
                    <td className="px-4 py-3">{payment.lessee_name}</td>
                    <td className="px-4 py-3 text-muted-foreground">{payment.payment_date}</td>
                    <td className="px-4 py-3 text-right">
                      {Number(payment.amount).toLocaleString()} {payment.currency}
                    </td>
                    <td className="px-4 py-3">{payment.method}</td>
                    <td className="px-4 py-3 text-center">
                      <Badge className={cn("text-xs", ALLOCATION_STATUS_COLORS[payment.allocation_status])}>
                        {payment.allocation_status.replace(/_/g, " ")}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-center">
                      {payment.voided && (
                        <Badge className="text-xs bg-red-100 text-red-800">
                          Voided
                        </Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
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
