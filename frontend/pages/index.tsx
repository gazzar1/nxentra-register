import Link from "next/link";
import { useEffect } from "react";

export default function HomePage() {
  // Force light mode on this page
  useEffect(() => {
    const root = document.documentElement;
    root.classList.add("light");

    return () => {
      // Restore user's theme preference on unmount
      try {
        const savedTheme = localStorage.getItem("nxentra-theme");
        if (savedTheme === "dark") {
          root.classList.remove("light");
        }
      } catch {
        root.classList.remove("light");
      }
    };
  }, []);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-slate-50 px-6 py-20 text-center text-slate-900">
      <div className="max-w-4xl space-y-8">
        <h1 className="text-4xl font-bold sm:text-5xl text-slate-900">
          Nxentra Smart ERP Access Platform
        </h1>
        <p className="text-lg text-slate-600">
          Manage registration, login, and tenant onboarding for your ERP workspace.
          Deploy the modern Next.js front-end on Vercel and serve the secure Django REST API from Digital Ocean.
        </p>
        <div className="flex flex-wrap items-center justify-center gap-4">
          <Link
            href="/register"
            className="rounded-full bg-accent px-6 py-3 font-semibold text-white shadow-lg shadow-accent/30 transition hover:bg-sky-400"
          >
            Create Company Workspace
          </Link>
          <Link
            href="/login"
            className="rounded-full border border-slate-300 px-6 py-3 font-semibold text-slate-900 transition hover:border-accent hover:bg-slate-100"
          >
            Login to Existing Workspace
          </Link>
        </div>
      </div>
    </main>
  );
}
