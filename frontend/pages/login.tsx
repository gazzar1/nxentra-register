import Link from "next/link";
import { FormEvent, useState, useEffect, useRef } from "react";
import { useRouter } from "next/router";
import { AuthLayout } from "@/components/AuthLayout";
import { InputField } from "@/components/FormField";
import { login } from "@/lib/api";
import { storeTokens } from "@/lib/auth-storage";

interface LoginResponse {
  access?: string;
  refresh?: string;
  detail?: string;
  pending_login_token?: string;
  companies?: Array<{
    id: number;
    public_id: string;
    name: string;
    role: string;
  }>;
}

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isDemo, setIsDemo] = useState(false);
  const demoTriggered = useRef(false);

  // B18 (2026-06-07): preserve `?next=` when linking to /register so a
  // merchant who came from the iframe's "Open Nxentra" button + clicked
  // "Get started" doesn't lose the /shopify/settings destination.
  const nextParam =
    typeof router.query.next === "string" && router.query.next.startsWith("/")
      ? router.query.next
      : "";
  const withNext = (path: string) =>
    nextParam ? `${path}?next=${encodeURIComponent(nextParam)}` : path;

  // Auto-fill demo credentials when ?demo=true
  useEffect(() => {
    if (router.query.demo === "true" && !demoTriggered.current) {
      demoTriggered.current = true;
      setEmail("demo@nxentra.com");
      setPassword("demo1234");
      setIsDemo(true);
    }
  }, [router.query.demo]);

  // Auto-submit when demo credentials are set
  useEffect(() => {
    if (isDemo && email === "demo@nxentra.com" && password === "demo1234") {
      const timer = setTimeout(() => {
        const form = document.querySelector("form");
        if (form) form.requestSubmit();
      }, 500);
      return () => clearTimeout(timer);
    }
  }, [isDemo, email, password]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);

    try {
      setIsSubmitting(true);
      const response = await login(email, password) as LoginResponse;

      // B6 (2026-06-05) / B18 (2026-06-07): preserve ?next= through the
      // login chain so post-login redirects land where the user was
      // originally headed (e.g. /shopify/settings after the iframe's
      // "Open Nxentra" + "Sign in" chain). Without this, the merchant
      // gets dumped on /dashboard. nextParam is shadowed here so the
      // top-of-component withNext() helper (used for the "Get started"
      // cross-link) and this handler stay self-contained.
      const rawNext = typeof router.query.next === "string" ? router.query.next : "";
      const handlerNext = rawNext && rawNext.startsWith("/") ? rawNext : "";

      // Handle "choose_company" response - user has multiple companies, no active set.
      // Backend returns 200 with a short-lived signed pending_login_token; the browser
      // exchanges that token + company_id for JWTs on /select-company. Password is
      // never stored client-side (A87).
      if (
        response.detail === "choose_company" &&
        response.companies &&
        response.pending_login_token
      ) {
        sessionStorage.setItem("pendingCompanies", JSON.stringify(response.companies));
        sessionStorage.setItem("pendingLoginToken", response.pending_login_token);
        const selectUrl = handlerNext
          ? `/select-company?from=login&next=${encodeURIComponent(handlerNext)}`
          : "/select-company?from=login";
        router.push(selectUrl);
        return;
      }

      // Direct token issuance — a single-company user (F2) now gets tokens on
      // the first login instead of a one-option chooser. Full-page redirect
      // (NOT router.push) so AuthContext re-initializes with the new tokens —
      // otherwise the persistent context stays unauthenticated and /dashboard
      // bounces straight back to /login. Mirrors select-company's approach.
      if (response.access && response.refresh) {
        storeTokens(response.access, response.refresh);
        const dest = handlerNext || "/dashboard";
        if (dest.startsWith("/")) {
          window.location.href = dest;
        } else {
          window.location.href = "/dashboard";
        }
        return;
      }

      // Unexpected response
      setError("Unexpected response from server.");
    } catch (loginError: unknown) {
      console.error(loginError);
      const axiosError = loginError as { response?: { data?: { detail?: string; message?: string } } };
      const detail = axiosError.response?.data?.detail;

      // Handle verification and approval errors
      if (detail === "email_not_verified") {
        router.push(`/verify-email?email=${encodeURIComponent(email)}`);
        return;
      }

      if (detail === "pending_approval") {
        router.push("/pending-approval");
        return;
      }

      if (detail === "no_company_access") {
        setError("You don't have access to any companies. Please contact an administrator.");
        return;
      }

      // Default error message
      setError("Invalid credentials. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <AuthLayout>
      <form onSubmit={handleSubmit} className="space-y-6">
        <div>
          <h2 className="text-2xl font-semibold text-foreground">Sign in</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            Access your accounting workspace.
          </p>
        </div>
        <InputField id="email" label="Email" type="email" value={email} onChange={setEmail} />
        <InputField id="password" label="Password" type="password" value={password} onChange={setPassword} />

        {error && <p className="text-sm text-destructive">{error}</p>}

        <button
          type="submit"
          className="w-full rounded-full bg-accent px-8 py-3 text-center font-semibold text-accent-foreground shadow-lg shadow-accent/30 transition hover:bg-accent/90"
          disabled={isSubmitting}
        >
          {isSubmitting ? "Authenticating..." : "Login"}
        </button>
        <p className="text-sm text-muted-foreground">
          Don&apos;t have an account? <Link href={withNext("/register")}>Get started</Link>
        </p>
      </form>
    </AuthLayout>
  );
}
