import Link from "next/link";
import { useRouter } from "next/router";
import { useTranslation } from "next-i18next";
import {
  LayoutDashboard, BookOpen, FileText, BarChart3, Users, Settings,
  Building2, Calendar, Layers, ChevronDown, ChevronRight, Upload, Plug,
  ShieldCheck, Database, UserCheck, X, ClipboardList, UserCircle, Truck,
  ShoppingCart, Receipt, Package, Percent, CreditCard, KeyRound, Mic,
  Warehouse, PackageOpen, ScrollText, Scale, PackagePlus, Wrench, Home,
  DoorOpen, UserSquare2, FileSignature, Banknote, AlertTriangle, PieChart,
  LayoutGrid, Stethoscope, HeartPulse, CalendarCheck, ClipboardCheck,
  ArrowLeftRight, Wallet, Boxes, Search, ShoppingBag, PackageCheck,
  ReceiptText, Calculator, Clock, Briefcase,
} from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { useSidebar } from "@/contexts/SidebarContext";
import { useSidebarNav, type SidebarSection, type SidebarTab } from "@/queries/useModules";
import { cn } from "@/lib/cn";
import { useState, useEffect, useRef, useMemo, useCallback } from "react";

let _savedScrollTop = 0;
let _savedExpanded: Record<string, boolean> = {};
let _savedTab: SidebarTab = "work";

const ICON_MAP: Record<string, React.ComponentType<{ className?: string }>> = {
  LayoutDashboard, BookOpen, FileText, BarChart3, Users, Settings,
  Building2, Calendar, Layers, Upload, Plug, ShieldCheck, Database,
  UserCheck, ClipboardList, UserCircle, Truck, ShoppingCart, Receipt,
  Package, Percent, CreditCard, KeyRound, Mic, Warehouse, PackageOpen,
  ScrollText, Scale, PackagePlus, Wrench, Home, DoorOpen, UserSquare2,
  FileSignature, Banknote, AlertTriangle, PieChart, LayoutGrid,
  Stethoscope, HeartPulse, CalendarCheck, ClipboardCheck, ArrowLeftRight,
  Wallet, Boxes, Search, ShoppingBag, PackageCheck, ReceiptText,
  Calculator, Clock, Briefcase,
};

const SECTION_COLORS: Record<string, string> = {
  work_finance: "text-emerald-500",
  work_sales: "text-orange-500",
  work_purchases: "text-violet-500",
  work_inventory: "text-teal-500",
  work_records: "text-blue-500",
  work_properties: "text-amber-600",
  work_clinic: "text-teal-600",
  work_shopify: "text-green-500",
  work_stripe: "text-purple-500",
  review_control: "text-red-500",
  review_statements: "text-pink-500",
  review_receivables_payables: "text-orange-500",
  review_analysis: "text-cyan-500",
  review_inventory: "text-teal-500",
  review_properties: "text-amber-600",
  setup_organization: "text-slate-500",
  setup_accounting: "text-emerald-500",
  setup_integrations: "text-blue-500",
  setup_inventory: "text-teal-500",
  setup_migration: "text-gray-500",
  setup_backups: "text-gray-500",
  setup_shopify: "text-green-500",
  setup_stripe: "text-purple-500",
  setup_banking: "text-blue-600",
  setup_properties: "text-amber-600",
  setup_clinic: "text-teal-600",
};

const TAB_CONFIG: { key: SidebarTab; label: string; icon: string }[] = [
  { key: "work", label: "Work", icon: "Briefcase" },
  { key: "review", label: "Review", icon: "BarChart3" },
  { key: "setup", label: "Setup", icon: "Settings" },
];

function getIcon(name: string, className: string) {
  const Icon = ICON_MAP[name];
  if (!Icon) return <FileText className={className} />;
  return <Icon className={className} />;
}

export function Sidebar() {
  const { t } = useTranslation("common");
  const router = useRouter();
  const { user, logout } = useAuth();
  const { isOpen, close } = useSidebar();
  const { data: sidebarData } = useSidebarNav();
  const [activeTab, setActiveTab] = useState<SidebarTab>(_savedTab);
  const [expanded, setExpanded] = useState<Record<string, boolean>>(_savedExpanded);
  const navRef = useRef<HTMLElement>(null);

  // Persist state across remounts
  useEffect(() => { _savedExpanded = expanded; }, [expanded]);
  useEffect(() => { _savedTab = activeTab; }, [activeTab]);

  // Get sections for active tab
  const sections = useMemo(() => {
    if (!sidebarData) return [];
    return sidebarData[activeTab] || [];
  }, [sidebarData, activeTab]);

  // Auto-expand section containing current route + auto-switch tab
  useEffect(() => {
    if (!sidebarData) return;
    const path = router.pathname;

    for (const tab of ["work", "review", "setup"] as SidebarTab[]) {
      const tabSections = sidebarData[tab] || [];
      for (const section of tabSections) {
        if (section.nav_items.some((item) => path === item.href || path.startsWith(item.href + "/"))) {
          if (activeTab !== tab) setActiveTab(tab);
          setExpanded((prev) => {
            if (prev[section.key]) return prev;
            return { ...prev, [section.key]: true };
          });
          return;
        }
      }
    }
  }, [router.pathname, sidebarData]); // eslint-disable-line react-hooks/exhaustive-deps

  // Scroll preservation
  const handleScroll = useCallback(() => {
    if (navRef.current) _savedScrollTop = navRef.current.scrollTop;
  }, []);

  useEffect(() => {
    const nav = navRef.current;
    if (nav && _savedScrollTop > 0) {
      requestAnimationFrame(() => { nav.scrollTop = _savedScrollTop; });
    }
  }, [sections]);

  // Close mobile sidebar on navigation
  useEffect(() => {
    const h = () => close();
    router.events.on("routeChangeComplete", h);
    return () => { router.events.off("routeChangeComplete", h); };
  }, [router.events, close]);

  const isActive = (href?: string) => {
    if (!href) return false;
    return router.pathname === href || router.pathname.startsWith(href + "/");
  };

  const isAdmin = user?.is_staff || user?.is_superuser;

  // Admin sections (hardcoded, not from API)
  const adminSection = useMemo(() => {
    if (!isAdmin) return null;
    const items = [
      { label: "Pending Users", href: "/admin/pending-users", icon: "UserCheck" },
      { label: "Projections", href: "/admin/projections", icon: "Database" },
      { label: "Voice Settings", href: "/settings/voice", icon: "Mic" },
    ];
    if (user?.is_superuser) {
      items.unshift(
        { label: "Dashboard", href: "/admin", icon: "ShieldCheck" },
        { label: "All Companies", href: "/admin/companies", icon: "Building2" },
        { label: "All Users", href: "/admin/all-users", icon: "Users" },
        { label: "Audit Log", href: "/admin/audit-log", icon: "FileText" },
      );
    }
    return { key: "admin", label: "Admin", icon: "ShieldCheck", items };
  }, [isAdmin, user?.is_superuser]);

  const renderSection = (section: SidebarSection) => {
    const isExp = expanded[section.key];
    const color = SECTION_COLORS[section.key] || "text-gray-500";

    return (
      <div key={section.key}>
        <button
          onClick={() => setExpanded((prev) => ({ ...prev, [section.key]: !prev[section.key] }))}
          className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors hover:bg-muted text-muted-foreground hover:text-foreground"
        >
          {getIcon(section.icon, `h-5 w-5 ${color}`)}
          <span className="flex-1 text-start">{section.label}</span>
          {isExp ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>
        {isExp && (
          <div className="ms-4 mt-1 space-y-1">
            {section.nav_items.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                  isActive(item.href)
                    ? "bg-accent text-primary-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                )}
              >
                {getIcon(item.icon, "h-4 w-4 text-muted-foreground")}
                <span>{item.label}</span>
              </Link>
            ))}
          </div>
        )}
      </div>
    );
  };

  return (
    <>
      {isOpen && (
        <div className="fixed inset-0 z-40 bg-background/80 backdrop-blur-sm lg:hidden" onClick={close} />
      )}

      <aside
        className={cn(
          "fixed inset-y-0 start-0 z-50 flex w-64 flex-col border-e bg-card transition-transform duration-300 ease-in-out lg:static lg:h-full lg:!translate-x-0",
          isOpen ? "translate-x-0" : "-translate-x-full rtl:translate-x-full lg:!translate-x-0"
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
          <button onClick={close} className="rounded-lg p-2 hover:bg-muted lg:hidden" aria-label="Close menu">
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Tab selector */}
        <div className="flex border-b px-2 py-2 gap-1">
          {TAB_CONFIG.map((tab) => (
            <button
              key={tab.key}
              onClick={() => { setActiveTab(tab.key); _savedScrollTop = 0; }}
              className={cn(
                "flex-1 flex items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium transition-colors",
                activeTab === tab.key
                  ? "bg-accent text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground"
              )}
            >
              {getIcon(tab.icon, "h-3.5 w-3.5")}
              {tab.label}
            </button>
          ))}
        </div>

        {/* Dashboard link (always visible) */}
        <div className="px-4 pt-3 pb-1">
          <Link
            href="/dashboard"
            className={cn(
              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
              isActive("/dashboard")
                ? "bg-accent text-primary-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            )}
          >
            <LayoutDashboard className="h-5 w-5 text-blue-500" />
            <span>Dashboard</span>
          </Link>
        </div>

        {/* Navigation sections for active tab */}
        <nav ref={navRef} onScroll={handleScroll} className="flex-1 overflow-y-auto px-4 pb-4">
          <div className="space-y-1">
            {sections.map(renderSection)}

            {/* Admin section (only in Work tab) */}
            {activeTab === "work" && adminSection && (
              <div>
                <button
                  onClick={() => setExpanded((prev) => ({ ...prev, admin: !prev.admin }))}
                  className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors hover:bg-muted text-muted-foreground hover:text-foreground"
                >
                  {getIcon("ShieldCheck", "h-5 w-5 text-red-500")}
                  <span className="flex-1 text-start">Admin</span>
                  {expanded.admin ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                </button>
                {expanded.admin && (
                  <div className="ms-4 mt-1 space-y-1">
                    {adminSection.items.map((item) => (
                      <Link
                        key={item.href}
                        href={item.href}
                        className={cn(
                          "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                          isActive(item.href)
                            ? "bg-accent text-primary-foreground"
                            : "text-muted-foreground hover:bg-muted hover:text-foreground"
                        )}
                      >
                        {getIcon(item.icon, "h-4 w-4 text-muted-foreground")}
                        <span>{item.label}</span>
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </nav>

        {/* User info + Logout — always visible at bottom */}
        <div className="border-t px-4 py-3">
          <div className="flex items-center gap-3 px-3 py-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-muted shrink-0">
              <UserCircle className="h-4 w-4 text-muted-foreground" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium truncate">{user?.name || user?.email}</p>
              {user?.name && <p className="text-xs text-muted-foreground truncate">{user.email}</p>}
            </div>
          </div>
          <button
            onClick={async () => { close(); await logout(); }}
            className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-red-400 transition-colors hover:bg-red-500/10"
          >
            <DoorOpen className="h-4 w-4" />
            <span>Logout</span>
          </button>
        </div>
      </aside>
    </>
  );
}
