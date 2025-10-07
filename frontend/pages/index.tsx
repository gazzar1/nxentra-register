import Link from "next/link";

export default function HomePage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-slate-950 px-6 py-20 text-center text-slate-100">
      <div className="max-w-4xl space-y-8">
        <h1 className="text-4xl font-bold sm:text-5xl">
          Nxentra Smart ERP Access Platform
        </h1>
        <p className="text-lg text-slate-400">
          Manage registration, login, and tenant onboarding for your ERP workspace.
          Deploy the modern Next.js front-end on Vercel and serve the secure Django REST API from Digital Ocean.
        </p>
        <div className="flex flex-wrap items-center justify-center gap-4">
          <Link
            href="/register"
            className="rounded-full bg-accent px-6 py-3 font-semibold text-slate-950 shadow-lg shadow-accent/30 transition hover:bg-sky-400"
          >
            Create Company Workspace
          </Link>
          <Link
            href="/login"
            className="rounded-full border border-slate-700 px-6 py-3 font-semibold text-slate-100 transition hover:border-accent"
          >
            Login to Existing Workspace
          </Link>
        </div>
      </div>
    </main>
  );
}
