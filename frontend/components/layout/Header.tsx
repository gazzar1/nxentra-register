import { useRouter } from "next/router";
import { useTranslation } from "next-i18next";
import { LogOut, User, Settings, Building2, Menu } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { useSidebar } from "@/contexts/SidebarContext";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { CompanySwitcher } from "./CompanySwitcher";
import { ThemeToggle } from "@/components/ui/theme-toggle";

export function Header() {
  const { t } = useTranslation("common");
  const router = useRouter();
  const { user, logout } = useAuth();
  const { toggle } = useSidebar();

  const handleLogout = async () => {
    await logout();
  };

  return (
    <header className="flex h-16 items-center justify-between border-b bg-card px-4 md:px-6">
      {/* Left side - Menu button and Company Switcher */}
      <div className="flex items-center gap-2 md:gap-4">
        {/* Mobile menu button */}
        <Button
          variant="ghost"
          size="icon"
          onClick={toggle}
          className="lg:hidden"
          aria-label="Toggle menu"
        >
          <Menu className="h-5 w-5" />
        </Button>
        <CompanySwitcher />
      </div>

      {/* Right side */}
      <div className="flex items-center gap-4">
        {/* Theme Toggle */}
        <ThemeToggle />

        {/* Language Switcher */}
        <LanguageSwitcher />

        {/* User Menu */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-muted">
                <User className="h-4 w-4" />
              </div>
              <span className="hidden md:inline">{user?.name || user?.email}</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="font-normal">
              <div className="flex flex-col space-y-1">
                <p className="text-sm font-medium">{user?.name}</p>
                <p className="text-xs text-muted-foreground">{user?.email}</p>
              </div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => router.push("/profile")}>
              <User className="me-2 h-4 w-4" />
              <span>Profile</span>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => router.push("/settings")}>
              <Settings className="me-2 h-4 w-4" />
              <span>{t("nav.settings")}</span>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => router.push("/settings/company")}>
              <Building2 className="me-2 h-4 w-4" />
              <span>{t("nav.companySettings")}</span>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={handleLogout} className="text-red-400">
              <LogOut className="me-2 h-4 w-4" />
              <span>{t("nav.logout")}</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
