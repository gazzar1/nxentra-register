import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { useState, useEffect } from "react";
import { Building2, ChevronRight, LogOut } from "lucide-react";
import axios from "axios";
import { AuthLayout } from "@/components/AuthLayout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { LoadingSpinner } from "@/components/common";
import { authService } from "@/services/auth.service";
import { getAccessToken, storeTokens, removeTokens } from "@/lib/auth-storage";

interface CompanyOption {
  id: number;
  public_id: string;
  name: string;
  role: string;
}

export default function SelectCompanyPage() {
  const router = useRouter();
  const [companies, setCompanies] = useState<CompanyOption[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSelecting, setIsSelecting] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isFromLogin, setIsFromLogin] = useState(false);

  useEffect(() => {
    // Check if we're coming from login with pending credentials
    const fromLogin = router.query.from === "login";
    setIsFromLogin(fromLogin);

    if (fromLogin) {
      loadPendingCompanies();
    } else {
      fetchCompanies();
    }
  }, [router.query.from]);

  const loadPendingCompanies = () => {
    // Load companies from sessionStorage (stored by login page)
    const pendingCompanies = sessionStorage.getItem("pendingCompanies");
    if (pendingCompanies) {
      setCompanies(JSON.parse(pendingCompanies));
      setIsLoading(false);
    } else {
      // No pending companies, redirect to login
      router.push("/login");
    }
  };

  const fetchCompanies = async () => {
    const token = getAccessToken();
    if (!token) {
      router.push("/login");
      return;
    }

    try {
      setIsLoading(true);
      setError(null);

      // Fetch companies list
      const companiesResponse = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api"}/companies/`,
        {
          headers: {
            Authorization: `Bearer ${token}`,
          },
        }
      );

      if (!companiesResponse.ok) {
        throw new Error("Failed to fetch companies");
      }

      const companiesData = await companiesResponse.json();
      setCompanies(companiesData);

      // If user has only one company, auto-select it
      if (companiesData.length === 1) {
        await handleSelectCompany(companiesData[0].id);
      }
    } catch (err) {
      console.error(err);
      setError("Failed to load your companies. Please try again.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleSelectCompany = async (companyId: number) => {
    try {
      setIsSelecting(companyId);
      setError(null);

      if (isFromLogin) {
        // Re-login with selected company_id
        const email = sessionStorage.getItem("pendingEmail");
        const password = sessionStorage.getItem("pendingPassword");

        if (!email || !password) {
          setError("Session expired. Please login again.");
          router.push("/login");
          return;
        }

        const response = await axios.post(
          `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api"}/auth/login/`,
          { email, password, company_id: companyId }
        );

        // Clear pending credentials
        sessionStorage.removeItem("pendingCompanies");
        sessionStorage.removeItem("pendingEmail");
        sessionStorage.removeItem("pendingPassword");

        // Store tokens and do full page redirect (not client-side navigation)
        // This ensures AuthContext properly initializes with new tokens
        storeTokens(response.data.access, response.data.refresh);
        window.location.href = "/dashboard";
      } else {
        // Already authenticated, just switch company
        const { data } = await authService.switchCompany(companyId);
        storeTokens(data.tokens.access, data.tokens.refresh);
        // Full page reload to reinitialize auth context with new company tokens
        window.location.href = "/dashboard";
      }
    } catch (err) {
      console.error(err);
      setError("Failed to select company. Please try again.");
      setIsSelecting(null);
    }
  };

  const handleLogout = () => {
    // Clear any pending credentials
    sessionStorage.removeItem("pendingCompanies");
    sessionStorage.removeItem("pendingEmail");
    sessionStorage.removeItem("pendingPassword");
    removeTokens();
    router.push("/login");
  };

  const getRoleBadgeColor = (role: string) => {
    switch (role) {
      case "OWNER":
        return "bg-purple-100 text-purple-700";
      case "ADMIN":
        return "bg-blue-100 text-blue-700";
      default:
        return "bg-muted text-muted-foreground";
    }
  };

  return (
    <AuthLayout>
      <div className="space-y-6">
        <div className="text-center">
          <div className="flex justify-center mb-4">
            <Building2 className="h-12 w-12 text-accent" />
          </div>
          <h2 className="text-2xl font-semibold text-foreground">Select a Company</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            Choose which company workspace you want to access.
          </p>
        </div>

        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-center">
            <p className="text-sm text-destructive">{error}</p>
          </div>
        )}

        {isLoading ? (
          <div className="flex justify-center py-12">
            <LoadingSpinner size="lg" />
          </div>
        ) : companies.length === 0 ? (
          <div className="text-center py-8">
            <p className="text-muted-foreground mb-4">
              You don&apos;t have access to any companies yet.
            </p>
            <p className="text-sm text-muted-foreground">
              Contact an administrator to be added to a company, or create a new one.
            </p>
            <Button
              variant="outline"
              className="mt-4"
              onClick={() => router.push("/register")}
            >
              Create New Company
            </Button>
          </div>
        ) : (
          <div className="space-y-3">
            {companies.map((company) => (
              <Card
                key={company.id}
                className={`cursor-pointer transition-all hover:border-accent/50 ${
                  isSelecting === company.id ? "border-accent" : ""
                }`}
                onClick={() => !isSelecting && handleSelectCompany(company.id)}
              >
                <CardContent className="p-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-4">
                      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted">
                        <Building2 className="h-5 w-5 text-muted-foreground" />
                      </div>
                      <div>
                        <p className="font-medium text-foreground">{company.name}</p>
                        <span
                          className={`inline-block mt-1 px-2 py-0.5 rounded text-xs font-medium ${getRoleBadgeColor(
                            company.role
                          )}`}
                        >
                          {company.role}
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center">
                      {isSelecting === company.id ? (
                        <LoadingSpinner size="sm" />
                      ) : (
                        <ChevronRight className="h-5 w-5 text-muted-foreground" />
                      )}
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}

        <div className="pt-4 border-t border-border">
          <Button
            variant="ghost"
            className="w-full text-muted-foreground hover:text-foreground"
            onClick={handleLogout}
          >
            <LogOut className="mr-2 h-4 w-4" />
            Sign out
          </Button>
        </div>
      </div>
    </AuthLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
