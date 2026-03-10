import { PropsWithChildren, useEffect } from "react";
import { useRouter } from "next/router";
import { useAuth } from "@/contexts/AuthContext";
import { SidebarProvider } from "@/contexts/SidebarContext";
import { Sidebar } from "./Sidebar";
import { Header } from "./Header";
import { ModuleGuard } from "./ModuleGuard";
import { LoadingScreen } from "@/components/common/LoadingScreen";

export function AppLayout({ children }: PropsWithChildren) {
  const { isAuthenticated, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.replace("/login");
    }
  }, [isLoading, isAuthenticated, router]);

  if (isLoading) {
    return <LoadingScreen />;
  }

  if (!isAuthenticated) {
    return null;
  }

  return (
    <SidebarProvider>
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
