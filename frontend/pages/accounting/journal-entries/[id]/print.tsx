import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { useEffect } from "react";
import { useJournalEntry } from "@/queries/useJournalEntries";

const STATUS_LABELS: Record<string, string> = {
  INCOMPLETE: "Incomplete",
  DRAFT: "Draft",
  POSTED: "Posted",
  REVERSED: "Reversed",
};

const KIND_LABELS: Record<string, string> = {
  NORMAL: "Normal",
  OPENING: "Opening",
  CLOSING: "Closing",
  ADJUSTMENT: "Adjustment",
  REVERSAL: "Reversal",
};

export default function PrintJournalEntryPage() {
  const router = useRouter();
  const { id } = router.query;
  const { data: entry, isLoading } = useJournalEntry(parseInt(id as string));

  useEffect(() => {
    if (entry && !isLoading) {
      setTimeout(() => window.print(), 500);
    }
  }, [entry, isLoading]);

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return "—";
    return new Date(dateStr).toLocaleDateString(undefined, {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  };

  const formatNumber = (value: string | number) => {
    const num = parseFloat(String(value));
    if (num === 0) return "—";
    return num.toLocaleString(undefined, {
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

  if (!entry) {
    return (
      <div className="p-8 text-center">
        <p>Journal entry not found</p>
      </div>
    );
  }

  // Calculate totals
  const totalDebit = entry.lines.reduce((sum, line) => sum + parseFloat(line.debit || "0"), 0);
  const totalCredit = entry.lines.reduce((sum, line) => sum + parseFloat(line.credit || "0"), 0);

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
            <h1 className="text-2xl font-bold">JOURNAL ENTRY</h1>
            <p className="text-lg font-mono mt-1">
              {entry.entry_number ? `#${entry.entry_number}` : `#${entry.id}`}
            </p>
          </div>
          <div className="text-right">
            <p className="text-sm text-gray-600">Status</p>
            <p className="font-semibold">{STATUS_LABELS[entry.status] || entry.status}</p>
          </div>
        </div>

        {/* Entry Info */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          <div>
            <h3 className="text-sm font-semibold text-gray-600">DATE</h3>
            <p className="font-medium">{formatDate(entry.date)}</p>
          </div>
          <div>
            <h3 className="text-sm font-semibold text-gray-600">TYPE</h3>
            <p className="font-medium">{KIND_LABELS[entry.kind] || entry.kind}</p>
          </div>
          <div>
            <h3 className="text-sm font-semibold text-gray-600">CURRENCY</h3>
            <p className="font-medium">{entry.currency || "—"}</p>
          </div>
          <div>
            <h3 className="text-sm font-semibold text-gray-600">PERIOD</h3>
            <p className="font-medium">{entry.period || "—"}</p>
          </div>
        </div>

        {/* Memo */}
        {entry.memo && (
          <div className="mb-8 p-4 bg-gray-50 rounded">
            <h3 className="text-sm font-semibold text-gray-600 mb-1">MEMO</h3>
            <p>{entry.memo}</p>
          </div>
        )}

        {/* Lines Table */}
        <table className="w-full mb-8 text-sm">
          <thead>
            <tr className="border-b-2 border-gray-300">
              <th className="text-left py-2 w-[50px]">#</th>
              <th className="text-left py-2 w-[120px]">Account</th>
              <th className="text-left py-2">Description</th>
              <th className="text-right py-2 w-[120px]">Debit</th>
              <th className="text-right py-2 w-[120px]">Credit</th>
            </tr>
          </thead>
          <tbody>
            {entry.lines.map((line, idx) => (
              <tr key={line.public_id || idx} className="border-b border-gray-200">
                <td className="py-2 text-gray-500">{line.line_no}</td>
                <td className="py-2">
                  <span className="font-mono text-xs">{line.account_code}</span>
                  {line.account_name && (
                    <span className="block text-xs text-gray-500">{line.account_name}</span>
                  )}
                </td>
                <td className="py-2">
                  {line.description || "—"}
                  {(line.customer_name || line.vendor_name) && (
                    <span className="block text-xs text-gray-500">
                      {line.customer_name || line.vendor_name}
                    </span>
                  )}
                </td>
                <td className="py-2 text-right font-mono">
                  {parseFloat(line.debit || "0") > 0 ? formatNumber(line.debit) : ""}
                </td>
                <td className="py-2 text-right font-mono">
                  {parseFloat(line.credit || "0") > 0 ? formatNumber(line.credit) : ""}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t-2 border-gray-300 font-bold">
              <td colSpan={3} className="py-2 text-right">TOTAL</td>
              <td className="py-2 text-right font-mono">{formatNumber(totalDebit)}</td>
              <td className="py-2 text-right font-mono">{formatNumber(totalCredit)}</td>
            </tr>
          </tfoot>
        </table>

        {/* Posting Info */}
        {entry.posted_at && (
          <div className="mt-8 pt-4 border-t text-sm text-gray-600">
            <p>Posted: {new Date(entry.posted_at).toLocaleString()}</p>
            {entry.posted_by_email && <p>By: {entry.posted_by_email}</p>}
          </div>
        )}

        {entry.reversed_at && (
          <div className="mt-4 text-sm text-gray-600">
            <p>Reversed: {new Date(entry.reversed_at).toLocaleString()}</p>
          </div>
        )}

        {/* Print Button */}
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
