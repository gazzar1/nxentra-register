import { useEffect, useRef, useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { Loader2, CheckCircle2, AlertCircle } from "lucide-react";
import axios from "axios";
import apiClient from "@/lib/api-client";
import { setEmbeddedAccessToken } from "@/lib/embedded-auth";
import { setAuthenticated } from "@/lib/auth-storage";
import { useAuth } from "@/contexts/AuthContext";
import {
  getShopifyHostParam,
  getShopifySessionToken,
  getShopifyShopParam,
  isShopifyEmbedded,
  persistShopifyContext,
} from "@/lib/shopify-embed";

const baseURL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
const NXENTRA_STANDALONE_URL = "https://app.nxentra.com";

/**
 * B8.5 (2026-06-06): embedded-mode landing page.
 *
 * Flow when the merchant lands here from inside the Shopify admin iframe:
 *   1. Persist `host` and `shop` to sessionStorage so subsequent in-iframe
 *      navigations (which may drop URL params) still report embedded.
 *   2. Wait for App Bridge to populate `window.shopify.idToken`.
 *   3. Call shopify.idToken() to get a Shopify session token JWT.
 *   4. POST to /api/auth/shopify-session-login/ — backend verifies the
 *      JWT, finds the ACTIVE ShopifyStore for the shop_domain, and mints
 *      a Nxentra JWT pair for the store's company OWNER. We stash the
 *      access token in memory + sessionStorage so subsequent API calls
 *      authenticate via Authorization: Bearer (cookies don't ride along
 *      in the iframe).
 *   5. Force AuthContext to refetch /auth/me/ via refreshProfile() so
 *      React state (isAuthenticated, user, company) updates before the
 *      next route gates on it.
 *   6. POST to /api/shopify/token-exchange/ — this is now authenticated
 *      by the Bearer header. Backend refreshes the offline access_token
 *      for the merchant's company (idempotent: reuses the existing
 *      ShopifyStore row).
 *   7. Redirect to /shopify/settings?host=...&shop=...&connected=true
 *      (params preserved so downstream still sees embedded mode).
 *
 * No-connection case: if step 4 returns 404 `no_connection`, the merchant
 * has installed the app without first connecting from standalone Nxentra.
 * We show an error state with a button that uses App Bridge's redirect
 * API (or a popup fallback) to open Nxentra in a new top-level context.
 */
export default function ShopifyEmbeddedPage() {
  const router = useRouter();
  const { refreshProfile } = useAuth();
  const [status, setStatus] = useState<
    "idle" | "waiting" | "session_login" | "exchanging" | "success" | "error" | "no_connection"
  >("idle");
  const [error, setError] = useState<string | null>(null);
  const [shopDomain, setShopDomain] = useState<string>("");
  const calledRef = useRef(false);

  useEffect(() => {
    if (!router.isReady) return;
    if (calledRef.current) return;
    calledRef.current = true;

    const run = async () => {
      const shop = getShopifyShopParam() || "";
      const host = getShopifyHostParam() || "";
      setShopDomain(shop);

      if (!isShopifyEmbedded()) {
        setStatus("error");
        setError(
          "This page is meant to be opened from inside the Shopify admin. " +
            "Use /shopify/settings instead.",
        );
        return;
      }

      // Persist context BEFORE anything async so a reload mid-flow still
      // sees us as embedded.
      if (host || shop) {
        persistShopifyContext(host, shop);
      }

      setStatus("waiting");

      // App Bridge loads async via the CDN script (kicked off in
      // _document.tsx as the first <script> in <head>). Poll briefly
      // for window.shopify.idToken to appear (typically <500ms).
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

      // Step 1: session-login. Bare axios — interceptors would short-
      // circuit on missing Bearer; we're bootstrapping auth here.
      setStatus("session_login");
      let accessToken: string | null = null;
      try {
        const { data } = await axios.post<{
          access: string;
          refresh: string;
          shop_domain: string;
          company_id: number;
        }>(
          `${baseURL}/auth/shopify-session-login/`,
          { session_token: sessionToken },
          {
            withCredentials: true,
            headers: { "Content-Type": "application/json" },
          },
        );
        accessToken = data.access;
        setShopDomain(data.shop_domain);
        setEmbeddedAccessToken(accessToken);
        setAuthenticated(true);
      } catch (e: unknown) {
        const ax = e as { response?: { status?: number; data?: { detail?: string; message?: string } } };
        if (ax.response?.status === 404 && ax.response?.data?.detail === "no_connection") {
          setStatus("no_connection");
          return;
        }
        setStatus("error");
        setError(
          ax.response?.data?.message ||
            "Couldn't sign in to your Nxentra account from Shopify. Please try again.",
        );
        return;
      }

      // Step 2: force AuthContext to re-fetch /auth/me/ so state.user /
      // state.company / state.isAuthenticated update before the next
      // route gates on them. Without this, AppLayout sees the stale
      // initial-mount state (isAuthenticated:false) and bounces to /login.
      try {
        await refreshProfile();
      } catch {
        // refreshProfile swallows its own errors — if it failed silently
        // the next page will hit a 401, and the api-client interceptor
        // will re-mint via App Bridge. Acceptable fallback.
      }

      // Step 3: token-exchange. apiClient attaches the Bearer header we
      // just stashed.
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
          // Preserve host+shop in the redirect URL so the next page (and
          // all client-side navigations from it) still report as embedded.
          const next = new URLSearchParams();
          if (host) next.set("host", host);
          if (shop) next.set("shop", shop);
          next.set("connected", "true");
          router.replace(`/shopify/settings?${next.toString()}`);
        }, 800);
      } catch (e: unknown) {
        const ax = e as { response?: { status?: number; data?: { error?: string } } };
        setStatus("error");
        setError(
          ax.response?.data?.error ||
            "Failed to complete the Shopify connection. Please try again.",
        );
      }
    };

    run();
  }, [router, refreshProfile]);

  /**
   * Open Nxentra's sign-in flow INSIDE the iframe (not by escaping to
   * the top window). The merchant sees Shopify admin's chrome the
   * entire time; only the iframe content changes from "No Nxentra
   * account connected" to the Nxentra login form, then select-company,
   * then /shopify/settings.
   *
   * Why same-iframe (B18.3) instead of top-level navigation (B18.1):
   *   - Same-origin nav (our app to our login) is always allowed in
   *     the iframe sandbox — no popup blocker or top-nav restrictions.
   *   - The merchant never visually leaves Shopify admin. Earlier live
   *     test 2026-06-07 showed merchants felt disoriented when the
   *     entire browser tab dropped them at app.nxentra.com mid-flow.
   *   - Cookies set during the in-iframe login are partitioned per
   *     top-level origin (admin.shopify.com), so they don't leak from
   *     or to a standalone-tab Nxentra session — which is actually a
   *     security plus. API calls from the iframe attach those
   *     partitioned cookies and work normally.
   *   - The eventual OAuth click on /shopify/settings still escapes to
   *     top via redirectTopLevel (B13), since accounts.shopify.com
   *     can't render in an iframe. After OAuth, B17 brings the
   *     merchant back into the embedded admin URL.
   *
   * Defaults to /login (not /register) since the no_connection branch
   * is overwhelmingly hit by returning merchants — B18.2.
   */
  const openNxentraTop = () => {
    if (typeof window === "undefined") return;
    const target = `${NXENTRA_STANDALONE_URL}/login?next=/shopify/settings`;
    window.location.href = target;
  };

  // Bare layout — no AppLayout chrome since we're embedded inside
  // Shopify admin which provides its own.
  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-6">
      <div className="w-full max-w-md rounded-lg border border-border bg-card p-8 shadow-lg">
        <h1 className="mb-6 text-xl font-semibold text-foreground">
          Connecting Shopify
        </h1>

        {(status === "idle" || status === "waiting") && (
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
        )}

        {status === "session_login" && (
          <div className="flex items-start gap-3 text-muted-foreground">
            <Loader2 className="mt-0.5 h-5 w-5 animate-spin" />
            <div>
              <p className="font-medium text-foreground">Signing you in…</p>
              <p className="text-sm">Authenticating your Nxentra account via Shopify.</p>
            </div>
          </div>
        )}

        {status === "exchanging" && (
          <div className="flex items-start gap-3 text-muted-foreground">
            <Loader2 className="mt-0.5 h-5 w-5 animate-spin" />
            <div>
              <p className="font-medium text-foreground">Refreshing connection…</p>
              <p className="text-sm">Updating your Shopify access token.</p>
            </div>
          </div>
        )}

        {status === "success" && (
          <div className="flex items-start gap-3 text-green-500">
            <CheckCircle2 className="mt-0.5 h-5 w-5" />
            <div>
              <p className="font-medium">Connected to {shopDomain}.</p>
              <p className="text-sm text-muted-foreground">
                Loading your Shopify settings…
              </p>
            </div>
          </div>
        )}

        {status === "no_connection" && (
          <>
            <div className="mb-4 flex items-start gap-3 text-amber-500">
              <AlertCircle className="mt-0.5 h-5 w-5" />
              <div>
                <p className="font-medium">No Nxentra account is connected to {shopDomain} yet.</p>
                <p className="text-sm text-muted-foreground">
                  Sign in or create your Nxentra account, then come back here from
                  your Shopify admin to finish the connection.
                </p>
              </div>
            </div>
            <button
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
              onClick={openNxentraTop}
            >
              Open Nxentra
            </button>
          </>
        )}

        {status === "error" && (
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
              onClick={() => router.reload()}
            >
              Try again
            </button>
          </>
        )}
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
