import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { CheckCircle2, Zap, Shield, BarChart3, Globe, Clock } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/common";
import { useAuth } from "@/contexts/AuthContext";

const PLAN_FEATURES = [
  { icon: BarChart3, label: "Full double-entry accounting with trial balance, P&L, balance sheet" },
  { icon: Zap, label: "Shopify integration with automated journal entries and reconciliation" },
  { icon: Globe, label: "Multi-currency with automated FX revaluation" },
  { icon: Shield, label: "Event-sourced audit trail — every change is traceable" },
  { icon: Clock, label: "Month-end close wizard with pre-flight validation" },
  { icon: CheckCircle2, label: "Bilingual Arabic/English with full RTL support" },
];

export default function BillingPage() {
  const { t } = useTranslation(["common"]);
  const { company } = useAuth();

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Plan & Billing"
          subtitle="Manage your subscription"
        />

        {/* Current Plan */}
        <Card className="border-primary/30 bg-primary/5">
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-xl">Early Access — Pilot</CardTitle>
                <p className="text-sm text-muted-foreground mt-1">
                  You&apos;re part of the early access program.
                </p>
              </div>
              <Badge className="bg-primary text-primary-foreground text-base px-4 py-1">
                Free
              </Badge>
            </div>
          </CardHeader>
          <CardContent>
            <div className="rounded-lg bg-background/60 border p-4">
              <p className="text-sm">
                <span className="font-medium">Company:</span> {company?.name || "—"}
              </p>
              <p className="text-sm mt-1">
                <span className="font-medium">Status:</span>{" "}
                <Badge className="bg-green-100 text-green-800">Active</Badge>
              </p>
              <p className="text-sm text-muted-foreground mt-3">
                During the pilot period, all features are available at no cost.
                We&apos;ll notify you before any pricing changes take effect.
              </p>
            </div>
          </CardContent>
        </Card>

        {/* What's Included */}
        <Card>
          <CardHeader>
            <CardTitle>What&apos;s Included</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 sm:grid-cols-2">
              {PLAN_FEATURES.map((feature, idx) => (
                <div key={idx} className="flex items-start gap-3 py-2">
                  <feature.icon className="h-5 w-5 text-primary shrink-0 mt-0.5" />
                  <span className="text-sm">{feature.label}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Feedback / Pricing Input */}
        <Card className="border-blue-200 dark:border-blue-900 bg-blue-50/50 dark:bg-blue-950/20">
          <CardContent className="py-5">
            <div className="flex items-start gap-3">
              <Zap className="h-5 w-5 text-blue-600 mt-0.5 shrink-0" />
              <div>
                <p className="font-medium text-blue-900 dark:text-blue-200">Help us set the right price</p>
                <p className="text-sm text-blue-700 dark:text-blue-400 mt-1">
                  We&apos;re finalizing pricing for general availability. Your feedback as a pilot user
                  directly shapes what Nxentra costs. If you have thoughts on what this is worth to your
                  business, we&apos;d love to hear them.
                </p>
                <a href="mailto:support@nxentra.com?subject=Pricing Feedback">
                  <Button variant="outline" size="sm" className="mt-3">
                    Share Pricing Feedback
                  </Button>
                </a>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return { props: { ...(await serverSideTranslations(locale ?? "en", ["common"])) } };
};
