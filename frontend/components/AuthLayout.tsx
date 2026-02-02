import Link from "next/link";
import { PropsWithChildren, useEffect } from "react";

export function AuthLayout({ children }: PropsWithChildren) {
  // Force light mode on auth pages
  useEffect(() => {
    const root = document.documentElement;
    root.classList.add("light");

    return () => {
      // Restore user's saved theme on unmount
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
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-blue-50 via-white to-slate-100">
      <div className="w-full max-w-3xl rounded-3xl border border-border bg-card p-10 shadow-xl shadow-slate-200/60">
        <header className="mb-8 text-center">
          <Link href="/" className="text-3xl font-semibold text-accent">
            Nxentra ERP Access
          </Link>
          <p className="mt-2 text-sm text-muted-foreground">
            Secure multi-tenant onboarding for your smart ERP workspace.
          </p>
        </header>
        {children}
      </div>
    </div>
  );
}
