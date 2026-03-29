import Link from "next/link";
import { PropsWithChildren, useEffect } from "react";

export function AuthLayout({ children }: PropsWithChildren) {
  // Force dark mode on auth pages to match the landing page
  useEffect(() => {
    const root = document.documentElement;
    root.classList.remove("light");

    return () => {
      // Restore user's saved theme on unmount
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
    <div className="fixed inset-0 overflow-auto bg-[#0a0e1a]">
      <div className="flex min-h-full items-center justify-center py-8 px-4">
        <div className="w-full max-w-3xl rounded-3xl border border-white/10 bg-white/[0.03] p-10 shadow-xl shadow-black/30">
        <header className="mb-8 text-center">
          <Link href="/" className="text-3xl font-semibold bg-gradient-to-r from-blue-400 to-cyan-400 bg-clip-text text-transparent">
            Nxentra ERP Access
          </Link>
          <p className="mt-2 text-sm text-zinc-400">
            Secure multi-tenant onboarding for your smart ERP workspace.
          </p>
        </header>
        {children}
        </div>
      </div>
    </div>
  );
}
