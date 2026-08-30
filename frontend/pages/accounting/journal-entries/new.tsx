import { useRef } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { v4 as uuidv4 } from "uuid";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader, LoadingSpinner } from "@/components/common";
import {
  JournalEntryForm,
  type PilotAdjustmentSourceSelection,
} from "@/components/forms/JournalEntryForm";
import { useCreateJournalEntry, useSaveCompleteJournalEntry } from "@/queries/useJournalEntries";
import { useToast } from "@/components/ui/toaster";
import { getErrorMessage } from "@/lib/api-client";
import { isPilotAdjustmentSourceKind } from "@/lib/pilot-adjustments";
import type { JournalEntryCreatePayload, PilotAdjustmentSourceKind } from "@/types/journal";

// A5-PR4b: read ONLY the two whitelisted prefill params. Anything malformed,
// array-valued, blank, or an unknown kind is ignored. Raw source_module /
// source_document are never consumed.
function readPrefill(query: Record<string, string | string[] | undefined>): {
  adjustment_source_kind?: PilotAdjustmentSourceKind;
  adjustment_source_reference?: string;
} {
  const kindRaw = query.adjustment_source_kind;
  const refRaw = query.adjustment_source_reference;
  if (typeof kindRaw !== "string" || typeof refRaw !== "string") return {};
  const kind = kindRaw.trim();
  const reference = refRaw.trim();
  if (!kind || !reference) return {};
  if (!isPilotAdjustmentSourceKind(kind)) return {};
  return { adjustment_source_kind: kind, adjustment_source_reference: reference };
}

export default function NewJournalEntryPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const createEntry = useCreateJournalEntry();
  const saveComplete = useSaveCompleteJournalEntry();

  // A5-PR4b: one stable Idempotency-Key per mounted create-form session.
  // Generated lazily on the client (never during render / SSR), reused across
  // retries of this create attempt. On success the page navigates away, so the
  // key never carries into a different entry.
  const idempotencyKeyRef = useRef<string | null>(null);
  const getIdempotencyKey = () => {
    if (!idempotencyKeyRef.current) {
      idempotencyKeyRef.current = uuidv4();
    }
    return idempotencyKeyRef.current;
  };

  const handleSubmit = async (
    data: JournalEntryCreatePayload,
    saveAsDraft: boolean,
    source?: PilotAdjustmentSourceSelection
  ) => {
    try {
      const createPayload: JournalEntryCreatePayload = { ...data };
      // Only send a source when the operator supplied a complete pair.
      if (source && source.kind && source.reference) {
        createPayload.adjustment_source_kind = source.kind;
        createPayload.adjustment_source_reference = source.reference;
      }

      const result = await createEntry.mutateAsync({
        data: createPayload,
        idempotencyKey: getIdempotencyKey(),
      });

      if (saveAsDraft && result.data) {
        // Save-complete carries no source — the created row already owns it.
        // Its failure must NOT keep the operator on this form: the entry now
        // exists, and resubmitting the mounted form would replay the consumed
        // Idempotency-Key against an edited payload — a guaranteed conflict.
        // Navigate to the created entry either way; it can be completed there.
        try {
          await saveComplete.mutateAsync({
            id: result.data.id,
            data: {
              date: data.date,
              period: data.period,
              memo: data.memo,
              memo_ar: data.memo_ar,
              lines: data.lines,
            },
          });
        } catch (completeError) {
          toast({
            title: t("messages.error"),
            description: `The entry was created but could not be marked complete: ${getErrorMessage(
              completeError
            )}. It was saved as incomplete — finish it from the entry page.`,
            variant: "destructive",
          });
          router.push(`/accounting/journal-entries/${result.data.id}`);
          return;
        }
      }

      toast({
        title: t("messages.success"),
        description: t("messages.saved"),
        variant: "success",
      });
      router.push(`/accounting/journal-entries/${result.data.id}`);
    } catch (error) {
      toast({
        title: t("messages.error"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  // Wait for the router to hydrate query params before rendering the form, so a
  // prefilled source survives the first mount (no one-shot-reset race).
  if (!router.isReady) {
    return (
      <AppLayout>
        <div className="flex justify-center py-20">
          <LoadingSpinner size="lg" />
        </div>
      </AppLayout>
    );
  }

  const prefill = readPrefill(router.query);
  const initialData = Object.keys(prefill).length > 0 ? prefill : undefined;

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("accounting:journalEntries.createEntry")}
          subtitle={t("accounting:journalEntries.subtitle")}
        />

        <Card>
          <CardHeader>
            <CardTitle>{t("accounting:journalEntries.entryDetails")}</CardTitle>
          </CardHeader>
          <CardContent>
            <JournalEntryForm
              initialData={initialData}
              onSubmit={handleSubmit}
              isSubmitting={createEntry.isPending || saveComplete.isPending}
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
