import Link from "next/link";
import { PropsWithChildren } from "react";

export function AuthLayout({ children }: PropsWithChildren) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-900 via-slate-950 to-slate-900">
      <div className="w-full max-w-3xl rounded-3xl border border-slate-800 bg-slate-900/70 p-10 shadow-xl shadow-slate-900/60 backdrop-blur">
        <header className="mb-8 text-center">
          <Link href="/" className="text-3xl font-semibold text-accent">
            Nxentra ERP Access
          </Link>
          <p className="mt-2 text-sm text-slate-400">
            Secure multi-tenant onboarding for your smart ERP workspace.
          </p>
        </header>
        {children}
      </div>
    </div>
  );
}
