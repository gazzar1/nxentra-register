import Link from "next/link";
import { FormEvent, useState } from "react";
import { useRouter } from "next/router";
import { AuthLayout } from "@/components/AuthLayout";
import { InputField } from "@/components/FormField";
import { login } from "@/lib/api";
import { storeTokens } from "@/lib/auth-storage";

interface LoginResponse {
  access?: string;
  refresh?: string;
  detail?: string;
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

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);

    try {
      setIsSubmitting(true);
      const response = await login(email, password) as LoginResponse;

      // Handle "choose_company" response - user has multiple companies, no active set
      // Backend returns 200 with companies list but NO tokens
      if (response.detail === "choose_company" && response.companies) {
        // Store companies in sessionStorage for the select-company page
        sessionStorage.setItem("pendingCompanies", JSON.stringify(response.companies));
        sessionStorage.setItem("pendingEmail", email);
        sessionStorage.setItem("pendingPassword", password);
        router.push("/select-company?from=login");
        return;
      }

      // Normal login with tokens
      if (response.access && response.refresh) {
        storeTokens(response.access, response.refresh);
        await router.push("/dashboard");
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
          <h2 className="text-2xl font-semibold text-foreground">Login to your workspace</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            Enter the email and password you used during onboarding.
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
          Need to onboard a workspace? <Link href="/register">Register</Link>
        </p>
      </form>
    </AuthLayout>
  );
}
