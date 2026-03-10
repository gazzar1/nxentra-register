import Link from "next/link";
import { useRouter } from "next/router";
import { useTranslation } from "next-i18next";
import {
  LayoutDashboard,
  BookOpen,
  FileText,
  BarChart3,
  Users,
  Settings,
  Building2,
  Calendar,
  Layers,
  ChevronDown,
  ChevronRight,
  Upload,
  Plug,
  ShieldCheck,
  Database,
  UserCheck,
  X,
  ClipboardList,
  UserCircle,
  Truck,
  ShoppingCart,
  Receipt,
  Package,
  Percent,
  CreditCard,
  KeyRound,
  Mic,
  Warehouse,
  PackageOpen,
  ScrollText,
  Scale,
  PackagePlus,
  Wrench,
  Home,
  DoorOpen,
  UserSquare2,
  FileSignature,
  Banknote,
  AlertTriangle,
  PieChart,
  LayoutGrid,
  Stethoscope,
  HeartPulse,
  CalendarCheck,
  ClipboardCheck,
} from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { useSidebar } from "@/contexts/SidebarContext";
import { useSidebarNav, type SidebarSection } from "@/queries/useModules";
import { cn } from "@/lib/cn";
import { useState, useEffect, useRef, useMemo } from "react";

// Map icon name strings from API to lucide-react components
const ICON_MAP: Record<string, React.ComponentType<{ className?: string }>> = {
  LayoutDashboard,
  BookOpen,
  FileText,
  BarChart3,
  Users,
  Settings,
  Building2,
  Calendar,
  Layers,
  Upload,
  Plug,
  ShieldCheck,
  Database,
  UserCheck,
  ClipboardList,
  UserCircle,
  Truck,
  ShoppingCart,
  Receipt,
  Package,
  Percent,
  CreditCard,
  KeyRound,
  Mic,
  Warehouse,
  PackageOpen,
  ScrollText,
  Scale,
  PackagePlus,
  Wrench,
  Home,
  DoorOpen,
  UserSquare2,
  FileSignature,
  Banknote,
  AlertTriangle,
  PieChart,
  LayoutGrid,
  Stethoscope,
  HeartPulse,
  CalendarCheck,
  ClipboardCheck,
};

// Color map per section key for visual distinction
const SECTION_ICON_COLORS: Record<string, string> = {
  dashboard: "text-blue-500",
  setup: "text-cyan-500",
  accounting: "text-emerald-500",
  sales: "text-orange-500",
  purchases: "text-violet-500",
  inventory: "text-teal-500",
  properties: "text-amber-600",
  clinic: "text-teal-600",
  reports: "text-pink-500",
  settings: "text-slate-500",
  admin: "text-red-500",
};

function getIcon(name: string, className: string) {
  const Icon = ICON_MAP[name];
  if (!Icon) return <FileText className={className} />;
  return <Icon className={className} />;
}

interface NavItem {
  label: string;
  href?: string;
  icon: React.ReactNode;
  children?: NavItem[];
}

function sectionsToNavItems(
  sections: SidebarSection[],
  t: (key: string, defaultValue?: any) => string,
): NavItem[] {
  return sections.map((section) => {
    const sectionColor = SECTION_ICON_COLORS[section.key] || "text-gray-500";

    // Dashboard is a single link, not a dropdown
    if (section.key === "dashboard") {
      return {
        label: t("nav.dashboard", section.label),
        href: "/dashboard",
        icon: getIcon(section.icon, `h-5 w-5 ${sectionColor}`),
      };
    }

    return {
      label: t(`nav.${section.key}`, section.label),
      icon: getIcon(section.icon, `h-5 w-5 ${sectionColor}`),
      children: section.nav_items.map((item) => ({
        label: item.translation_key ? t(item.translation_key, item.label) : item.label,
        href: item.href,
        icon: getIcon(item.icon, "h-4 w-4 text-muted-foreground"),
      })),
    };
  });
}

export function Sidebar() {
  const { t } = useTranslation("common");
  const router = useRouter();
  const { user } = useAuth();
  const { isOpen, close } = useSidebar();
  const { data: sections } = useSidebarNav();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  // Build nav items from API data
  const navItems = useMemo(() => {
    if (!sections) return [];
    return sectionsToNavItems(sections, t);
  }, [sections, t]);

  // Auto-expand the section containing the active route
  useEffect(() => {
    if (!sections) return;
    const path = router.pathname;

    for (const section of sections) {
      const prefixes = section.nav_items.map((item) => item.href);
      if (prefixes.some((prefix) => path === prefix || path.startsWith(prefix + "/"))) {
        const sectionLabel = t(`nav.${section.key}`, section.label).toLowerCase();
        setExpanded((prev) => {
          if (prev[sectionLabel]) return prev;
          return { ...prev, [sectionLabel]: true };
        });
        break;
      }
    }
  }, [router.pathname, sections, t]);

  // Ref for preserving sidebar scroll position
  const navRef = useRef<HTMLElement>(null);
  const scrollPosRef = useRef(0);

  // Save scroll position on every scroll so we never lose it
  useEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    const onScroll = () => { scrollPosRef.current = nav.scrollTop; };
    nav.addEventListener("scroll", onScroll, { passive: true });
    return () => nav.removeEventListener("scroll", onScroll);
  }, []);

  // After route change, close mobile menu (but do NOT reset scroll)
  useEffect(() => {
    const handleRouteChangeComplete = () => { close(); };
    router.events.on("routeChangeComplete", handleRouteChangeComplete);
    return () => { router.events.off("routeChangeComplete", handleRouteChangeComplete); };
  }, [router.events, close]);

  // Restore scroll position after any re-render that might reset it
  useEffect(() => {
    const nav = navRef.current;
    if (!nav || scrollPosRef.current === 0) return;
    // Use rAF to restore after the browser has painted
    const id = requestAnimationFrame(() => {
      nav.scrollTop = scrollPosRef.current;
    });
    return () => cancelAnimationFrame(id);
  });

  const isAdmin = user?.is_staff || user?.is_superuser;

  // Add admin section (always local, not from API — admin is not a business module)
  const allNavItems = useMemo(() => {
    const items = [...navItems];
    if (isAdmin) {
      const adminChildren: NavItem[] = [
        { label: t("nav.pendingUsers", "Pending Users"), href: "/admin/pending-users", icon: getIcon("UserCheck", "h-4 w-4 text-amber-400") },
        { label: t("nav.projections", "Projections"), href: "/admin/projections", icon: getIcon("Database", "h-4 w-4 text-cyan-400") },
        { label: t("nav.voiceSettings", "Voice Settings"), href: "/settings/voice", icon: getIcon("Mic", "h-4 w-4 text-violet-400") },
      ];
      if (user?.is_superuser) {
        adminChildren.unshift(
          { label: t("nav.adminDashboard", "Dashboard"), href: "/admin", icon: getIcon("ShieldCheck", "h-4 w-4 text-red-400") },
          { label: t("nav.allCompanies", "All Companies"), href: "/admin/companies", icon: getIcon("Building2", "h-4 w-4 text-purple-400") },
          { label: t("nav.allUsers", "All Users"), href: "/admin/all-users", icon: getIcon("Users", "h-4 w-4 text-indigo-400") },
          { label: t("nav.auditLog", "Audit Log"), href: "/admin/audit-log", icon: getIcon("FileText", "h-4 w-4 text-emerald-400") },
        );
      }
      items.push({
        label: t("nav.admin", "Admin"),
        icon: getIcon("ShieldCheck", "h-5 w-5 text-red-500"),
        children: adminChildren,
      });
    }
    return items;
  }, [navItems, isAdmin, user?.is_superuser, t]);

  const toggleExpand = (label: string) => {
    setExpanded((prev) => ({ ...prev, [label]: !prev[label] }));
  };

  const isActive = (href?: string) => {
    if (!href) return false;
    return router.pathname === href || router.pathname.startsWith(href + "/");
  };

  const renderNavItem = (item: NavItem, depth = 0) => {
    const hasChildren = item.children && item.children.length > 0;
    const isExpanded = expanded[item.label.toLowerCase()];
    const active = isActive(item.href);

    if (hasChildren) {
      return (
        <div key={item.label}>
          <button
            onClick={() => toggleExpand(item.label.toLowerCase())}
            className={cn(
              "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
              "hover:bg-muted text-muted-foreground hover:text-foreground"
            )}
          >
            {item.icon}
            <span className="flex-1 text-start">{item.label}</span>
            {isExpanded ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
          </button>
          {isExpanded && (
            <div className="ms-4 mt-1 space-y-1">
              {item.children!.map((child) => renderNavItem(child, depth + 1))}
            </div>
          )}
        </div>
      );
    }

    return (
      <Link
        key={item.label}
        href={item.href!}
        className={cn(
          "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
          active
            ? "bg-accent text-primary-foreground"
            : "text-muted-foreground hover:bg-muted hover:text-foreground"
        )}
      >
        {item.icon}
        <span>{item.label}</span>
      </Link>
    );
  };

  return (
    <>
      {/* Mobile overlay */}
      {isOpen && (
        <div
          className="fixed inset-0 z-40 bg-background/80 backdrop-blur-sm lg:hidden"
          onClick={close}
        />
      )}

      {/* Sidebar */}
      <aside
        className={cn(
          "fixed inset-y-0 start-0 z-50 flex w-64 flex-col border-e bg-card transition-transform duration-300 ease-in-out lg:static lg:h-full lg:translate-x-0",
          isOpen ? "translate-x-0" : "-translate-x-full rtl:translate-x-full"
        )}
      >
        {/* Logo */}
        <div className="flex h-16 items-center justify-between border-b px-6">
          <Link href="/dashboard" className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent">
              <span className="text-lg font-bold text-primary-foreground">N</span>
            </div>
            <span className="text-xl font-bold">Nxentra</span>
          </Link>
          <button
            onClick={close}
            className="rounded-lg p-2 hover:bg-muted lg:hidden"
            aria-label="Close menu"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Navigation */}
        <nav ref={navRef} className="flex-1 overflow-y-auto p-4">
          <div className="space-y-1">{allNavItems.map((item) => renderNavItem(item))}</div>
        </nav>
      </aside>
    </>
  );
}
