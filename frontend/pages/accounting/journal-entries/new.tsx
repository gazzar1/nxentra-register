import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/common";
import { JournalEntryForm } from "@/components/forms/JournalEntryForm";
import { useCreateJournalEntry, useSaveCompleteJournalEntry } from "@/queries/useJournalEntries";
import { useToast } from "@/components/ui/toaster";
import { getErrorMessage } from "@/lib/api-client";
import type { JournalEntryCreatePayload } from "@/types/journal";

export default function NewJournalEntryPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const createEntry = useCreateJournalEntry();
  const saveComplete = useSaveCompleteJournalEntry();

  const handleSubmit = async (data: JournalEntryCreatePayload, saveAsDraft: boolean) => {
    try {
      const result = await createEntry.mutateAsync(data);

      if (saveAsDraft && result.data) {
        // Mark as complete (DRAFT status)
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
