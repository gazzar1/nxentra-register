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
} from "lucide-react";
import { cn } from "@/lib/cn";
import { useState } from "react";

interface NavItem {
  label: string;
  href?: string;
  icon: React.ReactNode;
  children?: NavItem[];
}

export function Sidebar() {
  const { t } = useTranslation("common");
  const router = useRouter();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({
    accounting: true,
    reports: true,
    settings: true,
  });

  const navItems: NavItem[] = [
    {
      label: t("nav.dashboard"),
      href: "/dashboard",
      icon: <LayoutDashboard className="h-5 w-5" />,
    },
    {
      label: t("nav.accounting"),
      icon: <BookOpen className="h-5 w-5" />,
      children: [
        {
          label: t("nav.chartOfAccounts"),
          href: "/accounting/chart-of-accounts",
          icon: <FileText className="h-4 w-4" />,
        },
        {
          label: t("nav.journalEntries"),
          href: "/accounting/journal-entries",
          icon: <FileText className="h-4 w-4" />,
        },
        {
          label: t("nav.import", "Import Data"),
          href: "/accounting/import",
          icon: <Upload className="h-4 w-4" />,
        },
      ],
    },
    {
      label: t("nav.reports"),
      icon: <BarChart3 className="h-5 w-5" />,
      children: [
        {
          label: t("nav.trialBalance"),
          href: "/reports/trial-balance",
          icon: <BarChart3 className="h-4 w-4" />,
        },
        {
          label: t("nav.balanceSheet"),
          href: "/reports/balance-sheet",
          icon: <BarChart3 className="h-4 w-4" />,
        },
        {
          label: t("nav.incomeStatement"),
          href: "/reports/income-statement",
          icon: <BarChart3 className="h-4 w-4" />,
        },
      ],
    },
    {
      label: t("nav.users"),
      href: "/users",
      icon: <Users className="h-5 w-5" />,
    },
    {
      label: t("nav.settings"),
      icon: <Settings className="h-5 w-5" />,
      children: [
        {
          label: t("nav.companySettings"),
          href: "/settings/company",
          icon: <Building2 className="h-4 w-4" />,
        },
        {
          label: t("nav.periods"),
          href: "/settings/periods",
          icon: <Calendar className="h-4 w-4" />,
        },
        {
          label: t("nav.dimensions"),
          href: "/settings/dimensions",
          icon: <Layers className="h-4 w-4" />,
        },
        {
          label: t("nav.integrations", "Integrations"),
          href: "/settings/integrations",
          icon: <Plug className="h-4 w-4" />,
        },
        {
          label: t("nav.audit", "Event Audit"),
          href: "/settings/audit",
          icon: <ShieldCheck className="h-4 w-4" />,
        },
      ],
    },
  ];

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
            ? "bg-accent text-primary"
            : "text-muted-foreground hover:bg-muted hover:text-foreground"
        )}
      >
        {item.icon}
        <span>{item.label}</span>
      </Link>
    );
  };

  return (
    <aside className="flex h-screen w-64 flex-col border-e bg-card">
      {/* Logo */}
      <div className="flex h-16 items-center border-b px-6">
        <Link href="/dashboard" className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent">
            <span className="text-lg font-bold text-primary">N</span>
          </div>
          <span className="text-xl font-bold">Nxentra</span>
        </Link>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto p-4">
        <div className="space-y-1">{navItems.map((item) => renderNavItem(item))}</div>
      </nav>
    </aside>
  );
}
