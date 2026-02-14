import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { useEffect } from "react";
import { usePurchaseBill } from "@/queries/usePurchases";
import { BILL_STATUS_LABELS } from "@/types/purchases";

export default function PrintPurchaseBillPage() {
  const router = useRouter();
  const { id } = router.query;
  const { data: bill, isLoading } = usePurchaseBill(parseInt(id as string));

  useEffect(() => {
    if (bill && !isLoading) {
      // Auto-print when data loads
      setTimeout(() => window.print(), 500);
    }
  }, [bill, isLoading]);

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return "—";
    return new Date(dateStr).toLocaleDateString(undefined, {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  };

  const formatNumber = (value: string | number) => {
    return parseFloat(String(value)).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  };

  if (isLoading) {
    return (
      <div className="p-8 text-center">
        <p>Loading...</p>
      </div>
    );
  }

  if (!bill) {
    return (
      <div className="p-8 text-center">
        <p>Bill not found</p>
      </div>
    );
  }

  return (
    <>
      <style jsx global>{`
        @media print {
          body {
            -webkit-print-color-adjust: exact !important;
            print-color-adjust: exact !important;
          }
          .no-print { display: none !important; }
        }
        @media screen {
          .print-container { max-width: 800px; margin: 0 auto; }
        }
      `}</style>

      <div className="print-container p-8 bg-white min-h-screen">
        {/* Header */}
        <div className="flex justify-between items-start mb-8 border-b pb-4">
          <div>
            <h1 className="text-2xl font-bold">PURCHASE BILL</h1>
            <p className="text-lg font-mono mt-1">{bill.bill_number}</p>
          </div>
          <div className="text-right">
            <p className="text-sm text-gray-600">Status</p>
            <p className="font-semibold">{BILL_STATUS_LABELS[bill.status]}</p>
          </div>
        </div>

        {/* Vendor & Bill Info */}
        <div className="grid grid-cols-2 gap-8 mb-8">
          <div>
            <h3 className="text-sm font-semibold text-gray-600 mb-2">VENDOR</h3>
            <p className="font-semibold text-lg">{bill.vendor_name}</p>
            <p className="text-sm text-gray-600 font-mono">{bill.vendor_code}</p>
          </div>
          <div className="text-right">
            <div className="grid grid-cols-2 gap-2 text-sm">
              <span className="text-gray-600">Bill Date:</span>
              <span className="font-medium">{formatDate(bill.bill_date)}</span>
              <span className="text-gray-600">Due Date:</span>
              <span className="font-medium">{formatDate(bill.due_date)}</span>
              {bill.vendor_bill_reference && (
                <>
                  <span className="text-gray-600">Vendor Ref:</span>
                  <span className="font-medium">{bill.vendor_bill_reference}</span>
                </>
              )}
              <span className="text-gray-600">Posting Profile:</span>
              <span className="font-medium">{bill.posting_profile_code}</span>
            </div>
          </div>
        </div>

        {/* Line Items */}
        <table className="w-full mb-8 text-sm">
          <thead>
            <tr className="border-b-2 border-gray-300">
              <th className="text-left py-2 w-[40px]">#</th>
              <th className="text-left py-2">Description</th>
              <th className="text-left py-2 w-[80px]">Account</th>
              <th className="text-right py-2 w-[60px]">Qty</th>
              <th className="text-right py-2 w-[80px]">Unit Price</th>
              <th className="text-right py-2 w-[80px]">Discount</th>
              <th className="text-right py-2 w-[80px]">Net</th>
              <th className="text-right py-2 w-[80px]">Tax</th>
              <th className="text-right py-2 w-[90px]">Total</th>
            </tr>
          </thead>
          <tbody>
            {bill.lines.map((line) => (
              <tr key={line.id} className="border-b border-gray-200">
                <td className="py-2 text-gray-500">{line.line_number}</td>
                <td className="py-2">
                  <p className="font-medium">{line.description}</p>
                  {line.item_code && (
                    <p className="text-xs text-gray-500 font-mono">{line.item_code}</p>
                  )}
                </td>
                <td className="py-2 font-mono text-xs text-gray-600">{line.account_code}</td>
                <td className="py-2 text-right font-mono">{parseFloat(line.quantity).toLocaleString()}</td>
                <td className="py-2 text-right font-mono">{formatNumber(line.unit_price)}</td>
                <td className="py-2 text-right font-mono">
                  {parseFloat(line.discount_amount) > 0 ? formatNumber(line.discount_amount) : "—"}
                </td>
                <td className="py-2 text-right font-mono">{formatNumber(line.net_amount)}</td>
                <td className="py-2 text-right font-mono">
                  {formatNumber(line.tax_amount)}
                  {line.tax_code_code && (
                    <span className="text-xs text-gray-500 block">{line.tax_code_code}</span>
                  )}
                </td>
                <td className="py-2 text-right font-mono font-medium">{formatNumber(line.line_total)}</td>
              </tr>
            ))}
          </tbody>
        </table>

        {/* Totals */}
        <div className="flex justify-end">
          <div className="w-72 text-sm">
            <div className="flex justify-between py-1">
              <span className="text-gray-600">Subtotal</span>
              <span className="font-mono">{formatNumber(bill.subtotal)}</span>
            </div>
            <div className="flex justify-between py-1">
              <span className="text-gray-600">Total Discount</span>
              <span className="font-mono text-red-600">-{formatNumber(bill.total_discount)}</span>
            </div>
            <div className="flex justify-between py-1">
              <span className="text-gray-600">Total Tax</span>
              <span className="font-mono">{formatNumber(bill.total_tax)}</span>
            </div>
            <div className="flex justify-between py-2 border-t-2 border-gray-300 mt-2 text-lg font-bold">
              <span>Total Amount</span>
              <span className="font-mono">{formatNumber(bill.total_amount)}</span>
            </div>
          </div>
        </div>

        {/* Notes */}
        {bill.notes && (
          <div className="mt-8 pt-4 border-t">
            <h3 className="text-sm font-semibold text-gray-600 mb-2">NOTES</h3>
            <p className="text-sm">{bill.notes}</p>
          </div>
        )}

        {/* Journal Entry Reference */}
        {bill.posted_journal_entry && (
          <div className="mt-8 pt-4 border-t">
            <p className="text-sm text-gray-600">
              Journal Entry: <span className="font-mono">{bill.posted_journal_entry_number || `#${bill.posted_journal_entry}`}</span>
            </p>
            {bill.posted_at && (
              <p className="text-sm text-gray-600">Posted: {formatDate(bill.posted_at)}</p>
            )}
          </div>
        )}

        {/* Print Button (hidden when printing) */}
        <div className="no-print mt-8 text-center">
          <button
            onClick={() => window.print()}
            className="px-6 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            Print
          </button>
          <button
            onClick={() => window.close()}
            className="px-6 py-2 ml-4 border rounded hover:bg-gray-50"
          >
            Close
          </button>
        </div>
      </div>
    </>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common", "accounting"])),
    },
  };
};
