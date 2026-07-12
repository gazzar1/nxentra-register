// A166: the legacy /banking matcher UI is retired — it drove endpoints
// that double-posted against the canonical settlement path (A158) and
// its unmatch stranded reconciled journal lines with no event trail.
// Old links and bookmarks land on the canonical workspace instead.
import type { GetServerSideProps } from "next";

export const getServerSideProps: GetServerSideProps = async () => ({
  redirect: {
    destination: "/finance/reconciliation",
    permanent: false,
  },
});

export default function RetiredBankingReconciliationPage() {
  return null;
}
