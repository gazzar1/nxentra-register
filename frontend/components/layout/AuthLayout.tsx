import { PropsWithChildren, useEffect } from "react";
import Link from "next/link";
import { LanguageSwitcher } from "./LanguageSwitcher";

export function AuthLayout({ children }: PropsWithChildren) {
  // Force light mode on auth pages
  useEffect(() => {
    const root = document.documentElement;
    root.classList.add("light");

    return () => {
      // Restore user's saved theme on unmount
      try {
        const savedTheme = localStorage.getItem("nxentra-theme");
        if (savedTheme === "dark") {
          root.classList.remove("light");
        }
        // If savedTheme is "light" or null, keep the light class
      } catch {
        // Default to dark if localStorage is unavailable
        root.classList.remove("light");
      }
    };
  }, []);

  return (
    <div className="min-h-screen flex flex-col bg-background">
      {/* Header */}
      <header className="flex items-center justify-between p-4">
        <Link href="/" className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent">
            <span className="text-lg font-bold text-accent-foreground">N</span>
          </div>
          <span className="text-xl font-bold text-foreground">Nxentra</span>
        </Link>
        <LanguageSwitcher />
      </header>

      {/* Main content */}
      <main className="flex-1 flex items-center justify-center p-4">
        <div className="w-full max-w-md">
          {children}
        </div>
      </main>

      {/* Footer */}
      <footer className="p-4 text-center text-sm text-muted-foreground">
        <p>&copy; {new Date().getFullYear()} Nxentra. All rights reserved.</p>
      </footer>
    </div>
  );
}
