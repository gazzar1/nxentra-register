import Link from "next/link";
import { FormEvent, useState } from "react";
import { useRouter } from "next/router";
import { AuthLayout } from "@/components/AuthLayout";
import { InputField } from "@/components/FormField";
import { login } from "@/lib/api";
import { storeTokens } from "@/lib/auth-storage";

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
      const response = await login(email, password);
      storeTokens(response.access, response.refresh);
      await router.push("/profile");
    } catch (loginError) {
      console.error(loginError);
      setError("Invalid credentials. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <AuthLayout>
      <form onSubmit={handleSubmit} className="space-y-6">
        <div>
          <h2 className="text-2xl font-semibold text-slate-100">Login to your workspace</h2>
          <p className="mt-2 text-sm text-slate-400">
            Enter the email and password you used during onboarding.
          </p>
        </div>
        <InputField id="email" label="Email" type="email" value={email} onChange={setEmail} />
        <InputField id="password" label="Password" type="password" value={password} onChange={setPassword} />

        {error && <p className="text-sm text-red-400">{error}</p>}

        <button
          type="submit"
          className="w-full rounded-full bg-accent px-8 py-3 text-center font-semibold text-slate-950 shadow-lg shadow-accent/30 transition hover:bg-sky-400"
          disabled={isSubmitting}
        >
          {isSubmitting ? "Authenticating..." : "Login"}
        </button>
        <p className="text-sm text-slate-400">
          Need to onboard a workspace? <Link href="/register">Register</Link>
        </p>
      </form>
    </AuthLayout>
  );
}
