import { useEffect, useRef, useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { Loader2, CheckCircle2, AlertCircle } from "lucide-react";
import apiClient from "@/lib/api-client";
import {
  getShopifySessionToken,
  getShopifyShopParam,
  isShopifyEmbedded,
} from "@/lib/shopify-embed";
import { isAuthenticated as checkAuthFlag } from "@/lib/auth-storage";

/**
 * B8 (2026-06-05): embedded-mode landing page.
 *
 * When Shopify launches our app from the admin (inside the iframe),
 * the merchant lands here. The page:
 *   1. Reads the `shop` query param and detects embedded mode (?host=).
 *   2. Waits for App Bridge to populate `window.shopify`.
 *   3. Gets a session token via shopify.idToken().
 *   4. POSTs to /api/shopify/token-exchange/ with the token.
 *   5. Backend exchanges session_token for an offline access_token,
 *      persists/refreshes the ShopifyStore for the merchant's company.
 *   6. Redirects to /shopify/settings?connected=true.
 *
 * Auth gating: the token-exchange endpoint requires the merchant's
 * Nxentra JWT cookie. If unauthenticated, we redirect through
 * /login?next=<this URL with all params preserved> so the merchant
 * can sign in and come back.
 *
 * On error, show a fallback that lets the merchant retry or jump
 * to /shopify/settings to use the manual Connect form instead.
 */
export default function ShopifyEmbeddedPage() {
  const router = useRouter();
  const [status, setStatus] = useState<"idle" | "waiting" | "exchanging" | "success" | "error" | "needs_auth">(
    "idle",
  );
  const [error, setError] = useState<string | null>(null);
  const [shopDomain, setShopDomain] = useState<string>("");
  const calledRef = useRef(false);

  useEffect(() => {
    if (!router.isReady) return;
    if (calledRef.current) return;
    calledRef.current = true;

    const run = async () => {
      const shop = getShopifyShopParam() || "";
      setShopDomain(shop);

      if (!isShopifyEmbedded()) {
        setStatus("error");
        setError(
          "This page is meant to be opened from inside the Shopify admin. " +
            "Use /shopify/settings instead.",
        );
        return;
      }

      // Auth check: if we don't have a Nxentra session, bounce through
      // login with `next=` set so we come back to this exact URL after
      // login + select-company. The `host`+`shop` params are preserved
      // by the browser since they're in the current URL.
      if (!checkAuthFlag()) {
        setStatus("needs_auth");
        const current = window.location.pathname + window.location.search;
        router.replace(`/login?next=${encodeURIComponent(current)}`);
        return;
      }

      setStatus("waiting");

      // App Bridge loads async via the CDN script. Poll briefly for
      // window.shopify.idToken to appear (typically <500ms).
      const deadline = Date.now() + 5000;
      let sessionToken: string | null = null;
      while (Date.now() < deadline) {
        sessionToken = await getShopifySessionToken();
        if (sessionToken) break;
        await new Promise((resolve) => setTimeout(resolve, 200));
      }

      if (!sessionToken) {
        setStatus("error");
        setError(
          "Couldn't load Shopify App Bridge. Please reload the page from " +
            "your Shopify admin.",
        );
        return;
      }

      setStatus("exchanging");
      try {
        const { data } = await apiClient.post<{
          status: string;
          shop_domain: string;
          store_public_id: string;
        }>("/shopify/token-exchange/", {
          session_token: sessionToken,
          shop_domain: shop || undefined,
        });
        setShopDomain(data.shop_domain);
        setStatus("success");
        setTimeout(() => {
          router.replace("/shopify/settings?connected=true");
        }, 800);
      } catch (e: unknown) {
        const ax = e as { response?: { status?: number; data?: { error?: string } } };
        if (ax.response?.status === 401) {
          // JWT got invalidated between the auth check and the exchange.
          // Bounce through login to refresh.
          const current = window.location.pathname + window.location.search;
          router.replace(`/login?next=${encodeURIComponent(current)}`);
          return;
        }
        setStatus("error");
        setError(
          ax.response?.data?.error ||
            "Failed to complete the Shopify connection. Please try again.",
        );
      }
    };

    run();
  }, [router]);

  // Bare layout — no AppLayout chrome since we're embedded inside
  // Shopify admin which provides its own.
  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-6">
      <div className="w-full max-w-md rounded-lg border border-border bg-card p-8 shadow-lg">
        <h1 className="mb-6 text-xl font-semibold text-foreground">
          Connecting Shopify
        </h1>

        {status === "idle" || status === "waiting" ? (
          <div className="flex items-start gap-3 text-muted-foreground">
            <Loader2 className="mt-0.5 h-5 w-5 animate-spin" />
            <div>
              <p className="font-medium text-foreground">
                {status === "waiting" ? "Loading Shopify App Bridge…" : "Starting…"}
              </p>
              {shopDomain ? (
                <p className="text-sm">Connecting {shopDomain} to your Nxentra books.</p>
              ) : null}
            </div>
          </div>
        ) : null}

        {status === "exchanging" ? (
          <div className="flex items-start gap-3 text-muted-foreground">
            <Loader2 className="mt-0.5 h-5 w-5 animate-spin" />
            <div>
              <p className="font-medium text-foreground">Exchanging tokens…</p>
              <p className="text-sm">Setting up the connection on your books.</p>
            </div>
          </div>
        ) : null}

        {status === "needs_auth" ? (
          <div className="flex items-start gap-3 text-muted-foreground">
            <Loader2 className="mt-0.5 h-5 w-5 animate-spin" />
            <div>
              <p className="font-medium text-foreground">Signing you in…</p>
              <p className="text-sm">Taking you to Nxentra to sign in, then back here.</p>
            </div>
          </div>
        ) : null}

        {status === "success" ? (
          <div className="flex items-start gap-3 text-green-500">
            <CheckCircle2 className="mt-0.5 h-5 w-5" />
            <div>
              <p className="font-medium">Connected to {shopDomain}.</p>
              <p className="text-sm text-muted-foreground">
                Loading your Shopify settings…
              </p>
            </div>
          </div>
        ) : null}

        {status === "error" ? (
          <>
            <div className="mb-4 flex items-start gap-3 text-destructive">
              <AlertCircle className="mt-0.5 h-5 w-5" />
              <div>
                <p className="font-medium">Couldn&apos;t complete the connection.</p>
                <p className="text-sm text-muted-foreground">{error}</p>
              </div>
            </div>
            <button
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
              onClick={() => router.replace("/shopify/settings")}
            >
              Go to Shopify Settings
            </button>
          </>
        ) : null}
      </div>
    </main>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
