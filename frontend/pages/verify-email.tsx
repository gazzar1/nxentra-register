import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import { AuthLayout } from "@/components/AuthLayout";
import { verifyEmail, resendVerificationEmail } from "@/lib/api";

type VerificationStatus = 'loading' | 'verified' | 'pending_approval' | 'error' | 'sent';

export default function VerifyEmailPage() {
  const router = useRouter();
  const { token, email, sent } = router.query;

  const [status, setStatus] = useState<VerificationStatus>('loading');
  const [message, setMessage] = useState<string>('');
  const [resendCooldown, setResendCooldown] = useState(0);
  const [isResending, setIsResending] = useState(false);

  useEffect(() => {
    // If just registered, show "check your email" message
    if (sent === 'true' && email) {
      setStatus('sent');
      setMessage('Please check your email to verify your account.');
      return;
    }

    // If token provided, verify it
    if (token && typeof token === 'string') {
      handleVerification(token);
    } else if (!sent) {
      // No token and not just sent - show generic message
      setStatus('sent');
      setMessage('Please check your email for the verification link.');
    }
  }, [token, email, sent]);

  // Cooldown timer
  useEffect(() => {
    if (resendCooldown > 0) {
      const timer = setTimeout(() => setResendCooldown(resendCooldown - 1), 1000);
      return () => clearTimeout(timer);
    }
  }, [resendCooldown]);

  const handleVerification = async (verificationToken: string) => {
    try {
      setStatus('loading');
      const response = await verifyEmail(verificationToken);

      if (response.status === 'verified') {
        setStatus('verified');
        setMessage(response.message);
      } else if (response.status === 'pending_approval') {
        setStatus('pending_approval');
        setMessage(response.message);
      }
    } catch (error: unknown) {
      setStatus('error');
      const axiosError = error as { response?: { data?: { detail?: string } } };
      setMessage(axiosError.response?.data?.detail || 'Verification failed. The link may be expired or invalid.');
    }
  };

  const handleResend = async () => {
    if (!email || typeof email !== 'string' || resendCooldown > 0) return;

    try {
      setIsResending(true);
      await resendVerificationEmail(email);
      setMessage('A new verification email has been sent. Please check your inbox.');
      setResendCooldown(60); // 60 second cooldown
    } catch (error: unknown) {
      const axiosError = error as { response?: { data?: { detail?: string } } };
      setMessage(axiosError.response?.data?.detail || 'Failed to resend. Please try again later.');
    } finally {
      setIsResending(false);
    }
  };

  return (
    <AuthLayout>
      <div className="space-y-6 text-center">
        {status === 'loading' && (
          <>
            <div className="flex justify-center">
              <div className="h-16 w-16 animate-spin rounded-full border-4 border-accent border-t-transparent"></div>
            </div>
            <h2 className="text-2xl font-semibold text-slate-100">Verifying your email...</h2>
            <p className="text-slate-400">Please wait while we verify your email address.</p>
          </>
        )}

        {status === 'sent' && (
          <>
            <div className="flex justify-center">
              <svg className="h-16 w-16 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
              </svg>
            </div>
            <h2 className="text-2xl font-semibold text-slate-100">Check Your Email</h2>
            <p className="text-slate-400">{message}</p>
            {email && (
              <p className="text-sm text-slate-500">
                We sent a verification link to <span className="font-medium text-slate-300">{email}</span>
              </p>
            )}

            {email && (
              <div className="pt-4">
                <button
                  onClick={handleResend}
                  disabled={isResending || resendCooldown > 0}
                  className="text-sm text-accent hover:text-sky-400 disabled:text-slate-500 disabled:cursor-not-allowed"
                >
                  {isResending
                    ? 'Sending...'
                    : resendCooldown > 0
                    ? `Resend in ${resendCooldown}s`
                    : "Didn't receive it? Resend verification email"}
                </button>
              </div>
            )}

            <div className="pt-4">
              <Link href="/login" className="text-sm text-slate-400 hover:text-slate-300">
                Back to Login
              </Link>
            </div>
          </>
        )}

        {status === 'verified' && (
          <>
            <div className="flex justify-center">
              <svg className="h-16 w-16 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <h2 className="text-2xl font-semibold text-slate-100">Email Verified!</h2>
            <p className="text-slate-400">{message}</p>
            <Link
              href="/login"
              className="inline-block rounded-full bg-accent px-8 py-3 font-semibold text-slate-950 shadow-lg shadow-accent/30 transition hover:bg-sky-400"
            >
              Continue to Login
            </Link>
          </>
        )}

        {status === 'pending_approval' && (
          <>
            <div className="flex justify-center">
              <svg className="h-16 w-16 text-yellow-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <h2 className="text-2xl font-semibold text-slate-100">Pending Approval</h2>
            <p className="text-slate-400">{message}</p>
            <p className="text-sm text-slate-500">
              Your email has been verified. An administrator will review your account shortly.
            </p>
            <Link
              href="/login"
              className="inline-block rounded-full bg-accent px-8 py-3 font-semibold text-slate-950 shadow-lg shadow-accent/30 transition hover:bg-sky-400"
            >
              Back to Login
            </Link>
            <div className="pt-2">
              <Link href="/pending-approval" className="text-sm text-slate-400 hover:text-slate-300">
                Learn more about the approval process
              </Link>
            </div>
          </>
        )}

        {status === 'error' && (
          <>
            <div className="flex justify-center">
              <svg className="h-16 w-16 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            </div>
            <h2 className="text-2xl font-semibold text-slate-100">Verification Failed</h2>
            <p className="text-slate-400">{message}</p>
            <p className="text-sm text-slate-500">
              The verification link may have expired or already been used.
            </p>
            <Link
              href="/register"
              className="inline-block rounded-full bg-accent px-8 py-3 font-semibold text-slate-950 shadow-lg shadow-accent/30 transition hover:bg-sky-400"
            >
              Register Again
            </Link>
            <div className="pt-2">
              <Link href="/login" className="text-sm text-slate-400 hover:text-slate-300">
                Back to Login
              </Link>
            </div>
          </>
        )}
      </div>
    </AuthLayout>
  );
}
