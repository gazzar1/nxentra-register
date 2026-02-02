import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { useState } from "react";
import { ArrowLeft, Send, Undo2, Trash2, Pencil } from "lucide-react";
import Link from "next/link";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader, LoadingSpinner, StatusBadge, ConfirmDialog } from "@/components/common";
import {
  useJournalEntry,
  usePostJournalEntry,
  useReverseJournalEntry,
  useDeleteJournalEntry,
} from "@/queries/useJournalEntries";
import { useAuth } from "@/contexts/AuthContext";
import { useToast } from "@/components/ui/toaster";
import { getErrorMessage } from "@/lib/api-client";
import {
  canPostJournalEntry,
  canReverseJournalEntry,
  canEditJournalEntry,
  canDeleteJournalEntry,
} from "@/types/journal";

export default function JournalEntryDetailPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { company } = useAuth();
  const { toast } = useToast();
  const id = Number(router.query.id);

  const { data: entry, isLoading } = useJournalEntry(id);
  const postEntry = usePostJournalEntry();
  const reverseEntry = useReverseJournalEntry();
  const deleteEntry = useDeleteJournalEntry();

  const [showPostConfirm, setShowPostConfirm] = useState(false);
  const [showReverseConfirm, setShowReverseConfirm] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const formatCurrency = (amount: string) => {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: company?.default_currency || "USD",
      minimumFractionDigits: 2,
    }).format(parseFloat(amount));
  };

  const formatDate = (date: string) => {
    return new Date(date).toLocaleDateString(router.locale === "ar" ? "ar-SA" : "en-US");
  };

  const handlePost = async () => {
    try {
      await postEntry.mutateAsync(id);
      toast({
        title: t("messages.success"),
        description: t("accounting:messages.postSuccess"),
        variant: "success",
      });
      setShowPostConfirm(false);
    } catch (error) {
      toast({
        title: t("messages.error"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  const handleReverse = async () => {
    try {
      await reverseEntry.mutateAsync(id);
      toast({
        title: t("messages.success"),
        description: t("accounting:messages.reverseSuccess"),
        variant: "success",
      });
      setShowReverseConfirm(false);
    } catch (error) {
      toast({
        title: t("messages.error"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  const handleDelete = async () => {
    try {
      await deleteEntry.mutateAsync(id);
      toast({
        title: t("messages.success"),
        description: t("messages.deleted"),
        variant: "success",
      });
      router.push("/accounting/journal-entries");
    } catch (error) {
      toast({
        title: t("messages.error"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  if (isLoading) {
    return (
      <AppLayout>
        <div className="flex justify-center py-20">
          <LoadingSpinner size="lg" />
        </div>
      </AppLayout>
    );
  }

  if (!entry) {
    return (
      <AppLayout>
        <div className="text-center py-20">
          <p className="text-muted-foreground">{t("messages.noData")}</p>
          <Link href="/accounting/journal-entries">
            <Button variant="outline" className="mt-4">
              <ArrowLeft className="me-2 h-4 w-4" />
              {t("actions.back")}
            </Button>
          </Link>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={entry.entry_number ? `#${entry.entry_number}` : `#${entry.id}`}
          subtitle={entry.memo || t("accounting:journalEntries.entryDetails")}
          actions={
            <div className="flex items-center gap-2">
              {canEditJournalEntry(entry) && (
                <Button
                  variant="outline"
                  onClick={() => router.push(`/accounting/journal-entries/${id}/edit`)}
                >
                  <Pencil className="me-2 h-4 w-4" />
                  {t("actions.edit")}
                </Button>
              )}
              {canPostJournalEntry(entry) && (
                <Button onClick={() => setShowPostConfirm(true)}>
                  <Send className="me-2 h-4 w-4" />
                  {t("accounting:journalEntries.postEntry")}
                </Button>
              )}
              {canReverseJournalEntry(entry) && (
                <Button variant="outline" onClick={() => setShowReverseConfirm(true)}>
                  <Undo2 className="me-2 h-4 w-4" />
                  {t("accounting:journalEntries.reverseEntry")}
                </Button>
              )}
              {canDeleteJournalEntry(entry) && (
                <Button variant="destructive" onClick={() => setShowDeleteConfirm(true)}>
                  <Trash2 className="me-2 h-4 w-4" />
                  {t("actions.delete")}
                </Button>
              )}
            </div>
          }
        />

        {/* Entry Info */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              {t("accounting:journalEntries.entryDetails")}
              <StatusBadge status={entry.status} />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <div>
                <dt className="text-sm text-muted-foreground">{t("accounting:journalEntry.date")}</dt>
                <dd className="font-medium">{formatDate(entry.date)}</dd>
              </div>
              <div>
                <dt className="text-sm text-muted-foreground">{t("accounting:journalEntry.kind")}</dt>
                <dd className="font-medium">{t(`accounting:entryKinds.${entry.kind}`)}</dd>
              </div>
              <div>
                <dt className="text-sm text-muted-foreground">{t("accounting:journalEntry.currency")}</dt>
                <dd className="font-medium">{entry.currency || "-"}</dd>
              </div>
              <div>
                <dt className="text-sm text-muted-foreground">{t("accounting:journalEntry.status")}</dt>
                <dd><StatusBadge status={entry.status} /></dd>
              </div>
              {entry.memo && (
                <div className="sm:col-span-2">
                  <dt className="text-sm text-muted-foreground">{t("accounting:journalEntry.memo")}</dt>
                  <dd className="font-medium">{entry.memo}</dd>
                </div>
              )}
              {entry.posted_at && (
                <div>
                  <dt className="text-sm text-muted-foreground">{t("accounting:journalEntry.postedAt")}</dt>
                  <dd className="font-medium">{new Date(entry.posted_at).toLocaleString()}</dd>
                </div>
              )}
              {entry.reversed_at && (
                <div>
                  <dt className="text-sm text-muted-foreground">{t("accounting:journalEntry.reversedAt")}</dt>
                  <dd className="font-medium">{new Date(entry.reversed_at).toLocaleString()}</dd>
                </div>
              )}
            </dl>
          </CardContent>
        </Card>

        {/* Lines Table */}
        <Card>
          <CardHeader>
            <CardTitle>{t("accounting:journalEntries.lines")}</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-16">{t("accounting:journalLine.lineNo")}</TableHead>
                  <TableHead>{t("accounting:journalLine.account")}</TableHead>
                  <TableHead>{t("accounting:journalLine.description")}</TableHead>
                  <TableHead className="text-end">{t("accounting:journalLine.debit")}</TableHead>
                  <TableHead className="text-end">{t("accounting:journalLine.credit")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {entry.lines.map((line, idx) => (
                  <TableRow key={line.public_id || idx}>
                    <TableCell className="font-mono ltr-code">{line.line_no}</TableCell>
                    <TableCell>
                      <span className="font-mono ltr-code text-sm">{line.account_code}</span>
                      {line.account_name && (
                        <span className="ms-2 text-muted-foreground">{line.account_name}</span>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {line.description || "-"}
                    </TableCell>
                    <TableCell className="text-end ltr-number font-medium">
                      {parseFloat(line.debit) > 0 ? formatCurrency(line.debit) : "-"}
                    </TableCell>
                    <TableCell className="text-end ltr-number font-medium">
                      {parseFloat(line.credit) > 0 ? formatCurrency(line.credit) : "-"}
                    </TableCell>
                  </TableRow>
                ))}
                {/* Totals Row */}
                <TableRow className="font-bold border-t-2">
                  <TableCell colSpan={3} className="text-end">
                    {t("accounting:totals.totalDebit")} / {t("accounting:totals.totalCredit")}
                  </TableCell>
                  <TableCell className="text-end ltr-number">
                    {formatCurrency(entry.total_debit)}
                  </TableCell>
                  <TableCell className="text-end ltr-number">
                    {formatCurrency(entry.total_credit)}
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        {/* Back link */}
        <div>
          <Link href="/accounting/journal-entries">
            <Button variant="ghost">
              <ArrowLeft className="me-2 h-4 w-4" />
              {t("actions.back")}
            </Button>
          </Link>
        </div>
      </div>

      {/* Confirm Dialogs */}
      <ConfirmDialog
        open={showPostConfirm}
        onOpenChange={setShowPostConfirm}
        title={t("accounting:journalEntries.postEntry")}
        description={t("accounting:messages.postConfirm")}
        onConfirm={handlePost}
        isLoading={postEntry.isPending}
      />
      <ConfirmDialog
        open={showReverseConfirm}
        onOpenChange={setShowReverseConfirm}
        title={t("accounting:journalEntries.reverseEntry")}
        description={t("accounting:messages.reverseConfirm")}
        onConfirm={handleReverse}
        isLoading={reverseEntry.isPending}
      />
      <ConfirmDialog
        open={showDeleteConfirm}
        onOpenChange={setShowDeleteConfirm}
        title={t("accounting:journalEntries.deleteEntry")}
        description={t("messages.confirmDelete")}
        onConfirm={handleDelete}
        isLoading={deleteEntry.isPending}
        variant="destructive"
      />
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common", "accounting"])),
    },
  };
};
