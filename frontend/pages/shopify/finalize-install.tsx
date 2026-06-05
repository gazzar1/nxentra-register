import { useEffect, useRef, useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { Loader2, CheckCircle2, AlertCircle } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { shopifyService } from "@/services/shopify.service";

/**
 * B6 (2026-06-05): Shopify-initiated install completion page.
 *
 * The OAuth callback at /api/shopify/callback/ creates a
 * PendingShopifyInstall row (with the tokens already exchanged) and
 * redirects merchants here so they can pick which Nxentra company the
 * store should connect to. If the merchant is unauthenticated, the
 * front-end auth guard (or the call's 401 response) bounces them
 * through /login?next=/shopify/finalize-install?handle=... — login +
 * select-company now preserve `?next=` and land back here.
 *
 * On success: store row is created/updated, redirect to
 * /shopify/settings?connected=true.
 * On failure: surface the error with a retry option.
 */
export default function ShopifyFinalizeInstallPage() {
  const router = useRouter();
  const [status, setStatus] = useState<"idle" | "running" | "success" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [shopDomain, setShopDomain] = useState<string>("");
  const calledRef = useRef(false);

  useEffect(() => {
    if (!router.isReady) return;
    // The handle param is the PendingShopifyInstall.public_id (UUID).
    const handle = typeof router.query.handle === "string" ? router.query.handle : "";
    if (!handle) {
      setStatus("error");
      setError("Missing install handle. Please reinstall from Shopify.");
      return;
    }
    // Don't double-fire in React strict-mode dev re-renders.
    if (calledRef.current) return;
    calledRef.current = true;

    const finalize = async () => {
      setStatus("running");
      try {
        const { data } = await shopifyService.finalizeInstall(handle);
        setShopDomain(data.shop_domain);
        setStatus("success");
        // Short delay so the success state is visible before the redirect.
        setTimeout(() => {
          router.replace("/shopify/settings?connected=true");
        }, 800);
      } catch (e: unknown) {
        setStatus("error");
        const ax = e as { response?: { data?: { error?: string; detail?: string } } };
        const msg =
          ax.response?.data?.error ||
          ax.response?.data?.detail ||
          "Failed to finalize Shopify install.";
        setError(msg);
      }
    };
    finalize();
  }, [router]);

  return (
    <AppLayout>
      <div className="flex items-center justify-center py-12">
        <Card className="max-w-lg w-full">
          <CardHeader>
            <CardTitle>Completing Shopify Connection</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {status === "running" || status === "idle" ? (
              <div className="flex items-center gap-3 text-muted-foreground">
                <Loader2 className="h-5 w-5 animate-spin" />
                <span>Linking your Shopify store to this Nxentra company…</span>
              </div>
            ) : null}

            {status === "success" ? (
              <div className="flex items-start gap-3 text-green-500">
                <CheckCircle2 className="h-5 w-5 mt-0.5" />
                <div>
                  <p className="font-medium">Connected to {shopDomain}.</p>
                  <p className="text-sm text-muted-foreground">
                    Taking you to Shopify settings…
                  </p>
                </div>
              </div>
            ) : null}

            {status === "error" ? (
              <>
                <div className="flex items-start gap-3 text-destructive">
                  <AlertCircle className="h-5 w-5 mt-0.5" />
                  <div>
                    <p className="font-medium">Could not finalize the connection.</p>
                    <p className="text-sm text-muted-foreground">{error}</p>
                  </div>
                </div>
                <div className="flex gap-2">
                  <Button onClick={() => router.replace("/shopify/settings")}>
                    Go to Shopify Settings
                  </Button>
                </div>
              </>
            ) : null}
          </CardContent>
        </Card>
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
