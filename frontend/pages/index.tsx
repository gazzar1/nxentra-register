import type { GetServerSideProps } from "next";
import Link from "next/link";
import { useEffect } from "react";

// A122 (2026-06-02) + B8 (2026-06-05): when Shopify launches our app from a
// merchant's admin (clicking the app icon, App Store install, or embedded
// iframe load), it GETs `/` with signed query parameters
// (hmac/host/shop/session/timestamp). Without server-side handling here,
// the bare marketing page renders, Shopify reports the app as
// "application_cant_be_loaded_misconfigured", and merchants can't reach the
// app from their Shopify admin.
//
// Routing rules:
//   - `?host=` present  → embedded launch (we're inside the Shopify admin
//     iframe). 307 to /shopify/embedded — that client page picks up the
//     App Bridge session token and runs the Token Exchange flow.
//   - `?hmac=` or `?shop=` without `?host=` → legacy launch handshake,
//     route through the backend (HMAC verification → /shopify/settings).
//
// The `host` param is the canonical signal of an embedded launch. Shopify
// only sends it when our app is being loaded inside the admin iframe.
export const getServerSideProps: GetServerSideProps = async (ctx) => {
  const { hmac, host, shop } = ctx.query;

  if (host) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(ctx.query)) {
      if (typeof v === "string") params.set(k, v);
      else if (Array.isArray(v) && v.length) params.set(k, v[0]);
    }
    return {
      redirect: {
        destination: `/shopify/embedded?${params.toString()}`,
        permanent: false,
      },
    };
  }

  if (hmac || shop) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(ctx.query)) {
      if (typeof v === "string") params.set(k, v);
      else if (Array.isArray(v) && v.length) params.set(k, v[0]);
    }
    return {
      redirect: {
        destination: `/api/shopify/launch/?${params.toString()}`,
        permanent: false,
      },
    };
  }

  return { props: {} };
};

export default function HomePage() {
  // Force dark mode on this page to match the landing page
  useEffect(() => {
    const root = document.documentElement;
    root.classList.remove("light");

    return () => {
      // Restore user's theme preference on unmount
      try {
        const savedTheme = localStorage.getItem("nxentra-theme");
        if (savedTheme === "light") {
          root.classList.add("light");
        }
      } catch {
        // default is dark, do nothing
      }
    };
  }, []);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-[#0a0e1a] px-6 py-20 text-center">
      <div className="max-w-4xl space-y-8">
        <h1 className="text-4xl font-bold sm:text-5xl text-white">
          Nxentra
        </h1>
        <p className="text-lg text-zinc-400 max-w-2xl mx-auto">
          Reconciliation-first accounting for e-commerce merchants.
          Track orders, payouts, fees, and bank deposits clearly.
        </p>
        <div className="flex flex-wrap items-center justify-center gap-4">
          <Link
            href="/register"
            className="rounded-full bg-gradient-to-r from-blue-600 to-cyan-500 px-6 py-3 font-semibold text-white shadow-lg shadow-blue-500/20 transition hover:opacity-90"
          >
            Get Started
          </Link>
          <Link
            href="/login"
            className="rounded-full border border-white/10 px-6 py-3 font-semibold text-white transition hover:border-blue-500/50 hover:bg-white/5"
          >
            Sign In
          </Link>
        </div>
      </div>
    </main>
  );
}
