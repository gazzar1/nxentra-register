import { PropsWithChildren, useEffect } from "react";
import { useRouter } from "next/router";
import { useAuth } from "@/contexts/AuthContext";
import { SidebarProvider } from "@/contexts/SidebarContext";
import { Sidebar } from "./Sidebar";
import { Header } from "./Header";
import { ModuleGuard } from "./ModuleGuard";
import { CommandPalette } from "./CommandPalette";
import { LoadingScreen } from "@/components/common/LoadingScreen";
import { useShopifyEmbedded } from "@/lib/shopify-embed";

export function AppLayout({ children }: PropsWithChildren) {
  const { isAuthenticated, isLoading } = useAuth();
  const router = useRouter();
  // B8 (2026-06-05): inside the Shopify admin iframe we suppress our own
  // chrome (sidebar + header). Shopify renders its own admin chrome around
  // the iframe; ours would look nested and would also re-trigger the
  // sidebar inside the embedded launch flow. Detection is from `?host=`
  // in the URL which is the canonical Shopify-embedded signal.
  const isEmbedded = useShopifyEmbedded();

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      // B6 (2026-06-05): preserve the originally-requested URL so post-
      // login redirects land back here (e.g. /shopify/finalize-install
      // after a Shopify-initiated install). Avoid passing /login or
      // /dashboard as next.
      const current = router.asPath;
      const safeNext =
        current && current.startsWith("/") && !current.startsWith("/login") && current !== "/dashboard"
          ? current
          : "";
      router.replace(
        safeNext ? `/login?next=${encodeURIComponent(safeNext)}` : "/login",
      );
    }
  }, [isLoading, isAuthenticated, router]);

  if (isLoading) {
    return <LoadingScreen />;
  }

  if (!isAuthenticated) {
    return null;
  }

  if (isEmbedded) {
    // Bare content render inside the Shopify admin iframe — no sidebar,
    // no header, no command palette. The admin chrome wraps us already.
    return (
      <main className="h-screen overflow-y-auto bg-background p-4 md:p-6">
        <ModuleGuard>{children}</ModuleGuard>
      </main>
    );
  }

  return (
    <SidebarProvider>
      <CommandPalette />
      <div className="flex h-screen print:block print:h-auto">
        <div className="no-print">
          <Sidebar />
        </div>
        <div className="flex flex-1 flex-col overflow-hidden print:overflow-visible">
          <div className="no-print">
            <Header />
          </div>
          <main className="flex-1 overflow-y-auto p-4 md:p-6 print:p-0 print:overflow-visible">
            <ModuleGuard>{children}</ModuleGuard>
          </main>
        </div>
      </div>
    </SidebarProvider>
  );
}
