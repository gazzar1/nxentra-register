import Link from "next/link";
import { AuthLayout } from "@/components/AuthLayout";

export default function PendingApprovalPage() {
  return (
    <AuthLayout>
      <div className="space-y-6 text-center">
        <div className="flex justify-center">
          <svg className="h-20 w-20 text-yellow-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
            />
          </svg>
        </div>

        <div>
          <h2 className="text-2xl font-semibold text-slate-100">Account Pending Approval</h2>
          <p className="mt-4 text-slate-400">
            Thank you for verifying your email address. Your account is currently being reviewed by our team.
          </p>
        </div>

        <div className="rounded-lg border border-slate-700 bg-slate-800/50 p-6 text-left">
          <h3 className="font-medium text-slate-200">What happens next?</h3>
          <ul className="mt-4 space-y-3 text-sm text-slate-400">
            <li className="flex items-start gap-3">
              <svg className="mt-0.5 h-5 w-5 flex-shrink-0 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              <span>Our team will review your registration details</span>
            </li>
            <li className="flex items-start gap-3">
              <svg className="mt-0.5 h-5 w-5 flex-shrink-0 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              <span>You will receive an email notification once your account is approved</span>
            </li>
            <li className="flex items-start gap-3">
              <svg className="mt-0.5 h-5 w-5 flex-shrink-0 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              <span>After approval, you can log in and access your workspace</span>
            </li>
          </ul>
        </div>

        <div className="rounded-lg border border-slate-700 bg-slate-800/50 p-6 text-left">
          <h3 className="font-medium text-slate-200">Need help?</h3>
          <p className="mt-2 text-sm text-slate-400">
            If you have any questions about the approval process or need to expedite your registration,
            please contact our support team.
          </p>
          <a
            href="mailto:support@nxentra.com"
            className="mt-4 inline-flex items-center gap-2 text-sm text-accent hover:text-sky-400"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
            support@nxentra.com
          </a>
        </div>

        <div className="pt-4 space-y-2">
          <p className="text-sm text-slate-500">
            Already approved? Try logging in.
          </p>
          <Link
            href="/login"
            className="inline-block rounded-full bg-accent px-8 py-3 font-semibold text-slate-950 shadow-lg shadow-accent/30 transition hover:bg-sky-400"
          >
            Go to Login
          </Link>
        </div>
      </div>
    </AuthLayout>
  );
}
