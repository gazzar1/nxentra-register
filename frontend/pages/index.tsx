import Link from "next/link";
import { useEffect } from "react";

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
