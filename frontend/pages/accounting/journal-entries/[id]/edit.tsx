import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { PageHeader, LoadingSpinner } from "@/components/common";
import {
  JournalEntryForm,
  type PilotAdjustmentSourceSelection,
} from "@/components/forms/JournalEntryForm";
import {
  useJournalEntry,
  useUpdateJournalEntry,
  useSaveCompleteJournalEntry,
} from "@/queries/useJournalEntries";
import { useToast } from "@/components/ui/toaster";
import { getErrorMessage } from "@/lib/api-client";
import { canEditJournalEntry } from "@/types/journal";
import type { JournalEntryCreatePayload, JournalEntryUpdatePayload } from "@/types/journal";
import {
  parseSourceDocument,
  PILOT_ADJUSTMENT_SOURCE_MODULE,
} from "@/lib/pilot-adjustments";

export default function EditJournalEntryPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const id = Number(router.query.id);

  const { data: entry, isLoading } = useJournalEntry(id);
  const updateEntry = useUpdateJournalEntry();
  const saveComplete = useSaveCompleteJournalEntry();

  // A5-PR4b: classify the entry's CURRENT stamp.
  const isPilotAdjustment = entry?.source_module === PILOT_ADJUSTMENT_SOURCE_MODULE;
  const parsedSource = isPilotAdjustment ? parseSourceDocument(entry?.source_document) : null;
  const systemOwnedSource =
    entry && entry.source_module && entry.source_module !== PILOT_ADJUSTMENT_SOURCE_MODULE
      ? { module: entry.source_module, document: entry.source_document }
      : null;

  const handleSubmit = async (
    data: JournalEntryCreatePayload,
    saveAsDraft: boolean,
    source?: PilotAdjustmentSourceSelection
  ) => {
    try {
      const updatePayload: JournalEntryUpdatePayload = {
        date: data.date,
        period: data.period,
        memo: data.memo,
        memo_ar: data.memo_ar,
        lines: data.lines,
      };

      // A5-PR4b: send the source through PATCH only when it actually changed
      // (set / changed / cleared). Never send adjustment fields for a
      // system-owned draft — that would try to clear or relabel a system stamp
      // and be refused server-side.
      if (source && !systemOwnedSource) {
        const initialKind = parsedSource?.kind ?? "";
        const initialRef = parsedSource?.body ?? "";
        if (source.kind !== initialKind || source.reference !== initialRef) {
          updatePayload.adjustment_source_kind = source.kind;
          updatePayload.adjustment_source_reference = source.reference;
        }
      }

      await updateEntry.mutateAsync({ id, data: updatePayload });

      if (saveAsDraft) {
        // Mark as complete (DRAFT status). Save-complete carries no source.
        await saveComplete.mutateAsync({
          id,
          data: {
            date: data.date,
            period: data.period,
            memo: data.memo,
            memo_ar: data.memo_ar,
            lines: data.lines,
          },
        });
      }

      toast({
        title: t("messages.success"),
        description: t("messages.saved"),
        variant: "success",
      });
      router.push(`/accounting/journal-entries/${id}`);
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

  // Check if entry can be edited
  if (!canEditJournalEntry(entry)) {
    return (
      <AppLayout>
        <div className="text-center py-20">
          <p className="text-muted-foreground">
            {t("accounting:messages.cannotEditPosted", "Cannot edit posted or reversed entries")}
          </p>
          <Link href={`/accounting/journal-entries/${id}`}>
            <Button variant="outline" className="mt-4">
              <ArrowLeft className="me-2 h-4 w-4" />
              {t("actions.back")}
            </Button>
          </Link>
        </div>
      </AppLayout>
    );
  }

  // Convert entry data to form format
  const initialData = {
    date: entry.date,
    period: entry.period ?? undefined,
    memo: entry.memo,
    memo_ar: entry.memo_ar,
    // A5-PR4b: prefill the typed source when the entry is a pilot adjustment.
    ...(parsedSource
      ? {
          adjustment_source_kind: parsedSource.kind,
          adjustment_source_reference: parsedSource.body,
        }
      : {}),
    lines: entry.lines.map((line) => ({
      account_id: line.account,
      description: line.description || "",
      description_ar: line.description_ar || "",
      debit: parseFloat(line.debit) || 0,
      credit: parseFloat(line.credit) || 0,
      analysis_tags: line.analysis_tags?.map((tag) => ({
        dimension_id: tag.dimension_id,
        dimension_value_id: tag.dimension_value_id,
      })) || [],
    })),
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("accounting:journalEntries.editEntry", "Edit Journal Entry")}
          subtitle={entry.entry_number ? `#${entry.entry_number}` : `#${entry.id}`}
        />

        <Card>
          <CardHeader>
            <CardTitle>{t("accounting:journalEntries.entryDetails")}</CardTitle>
          </CardHeader>
          <CardContent>
            <JournalEntryForm
              initialData={initialData}
              onSubmit={handleSubmit}
              systemOwnedSource={systemOwnedSource}
              isSubmitting={updateEntry.isPending || saveComplete.isPending}
              onCancel={() => router.back()}
            />
          </CardContent>
        </Card>
      </div>
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
