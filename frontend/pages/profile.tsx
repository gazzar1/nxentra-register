import { useEffect, useState } from "react";
import { useRouter } from "next/router";
import { AuthLayout } from "@/components/AuthLayout";
import { getProfile, ProfileResponse, logout } from "@/lib/api";
import { clearTokens, getAccessToken, getRefreshToken } from "@/lib/auth-storage";

export default function ProfilePage() {
  const router = useRouter();
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchProfile = async () => {
      const accessToken = getAccessToken();
      if (!accessToken) {
        router.replace("/login");
        return;
      }

      try {
        const profileResponse = await getProfile(accessToken);
        setProfile(profileResponse);
      } catch (error) {
        console.error(error);
        router.replace("/login");
      } finally {
        setLoading(false);
      }
    };

    fetchProfile();
  }, [router]);

  const handleLogout = async () => {
    const refresh = getRefreshToken();
    if (refresh) {
      try {
        await logout(refresh);
      } catch (error) {
        console.error("Failed to revoke refresh token", error);
      }
    }
    clearTokens();
    router.replace("/login");
  };

  if (loading) {
    return (
      <AuthLayout>
        <div className="space-y-3 text-center">
          <p className="text-sm text-slate-400">Loading your workspace preferences...</p>
        </div>
      </AuthLayout>
    );
  }

  if (!profile) {
    return null;
  }

  const { user, company } = profile;

  return (
    <AuthLayout>
      <div className="space-y-8">
        <header>
          <h2 className="text-3xl font-semibold text-slate-100">Welcome back, {user.name}</h2>
          <p className="mt-2 text-sm text-slate-400">Here are the settings that power your Nxentra ERP tenant.</p>
        </header>

        <section className="rounded-3xl border border-slate-800 bg-slate-900/50 p-6">
          <h3 className="text-xl font-semibold text-slate-200">Company workspace</h3>
          <dl className="mt-4 grid grid-cols-1 gap-4 text-sm text-slate-300 md:grid-cols-2">
            <div>
              <dt className="text-slate-500">Database name</dt>
              <dd className="text-lg font-medium text-slate-100">{company.name}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Currency</dt>
              <dd className="text-lg font-medium text-slate-100">{company.currency}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Language</dt>
              <dd className="text-lg font-medium text-slate-100">{company.language === "ar" ? "Arabic" : "English"}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Accounting periods</dt>
              <dd className="text-lg font-medium text-slate-100">{company.periods}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Current period</dt>
              <dd className="text-lg font-medium text-slate-100">{company.current_period}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Thousands separator</dt>
              <dd className="text-lg font-medium text-slate-100">{company.thousand_separator || "None"}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Decimal places</dt>
              <dd className="text-lg font-medium text-slate-100">{company.decimal_places}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Decimal separator</dt>
              <dd className="text-lg font-medium text-slate-100">{company.decimal_separator}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Date format</dt>
              <dd className="text-lg font-medium text-slate-100">{company.date_format}</dd>
            </div>
          </dl>
        </section>

        <button
          onClick={handleLogout}
          className="w-full rounded-full border border-slate-700 px-8 py-3 font-semibold text-slate-100 transition hover:border-red-500 hover:text-red-400"
        >
          Logout
        </button>
      </div>
    </AuthLayout>
  );
}
