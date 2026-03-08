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
import { cn } from "@/lib/cn";
import { useState, useEffect, useRef } from "react";

interface NavItem {
  label: string;
  href?: string;
  icon: React.ReactNode;
  children?: NavItem[];
}

export function Sidebar() {
  const { t } = useTranslation("common");
  const router = useRouter();
  const { user } = useAuth();
  const { isOpen, close } = useSidebar();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  // Auto-expand the section containing the active route on mount/route change
  useEffect(() => {
    const path = router.pathname;
    const sectionForPath: Record<string, string[]> = {
      accounting: ["/accounting/journal-entries", "/accounting/scratchpad", "/accounting/import"],
      sales: ["/accounting/sales-invoices", "/accounting/receipts"],
      purchases: ["/accounting/purchase-bills", "/accounting/payments"],
      inventory: ["/inventory/balances", "/inventory/ledger", "/inventory/adjustments", "/inventory/opening-balance"],
      properties: ["/properties/dashboard", "/properties/properties", "/properties/units", "/properties/lessees", "/properties/leases", "/properties/payments", "/properties/expenses", "/properties/alerts", "/properties/reports"],
      clinic: ["/clinic/patients", "/clinic/doctors", "/clinic/visits", "/clinic/invoices", "/clinic/payments", "/clinic/settings"],
      reports: ["/reports"],
      setup: ["/settings/periods", "/accounting/chart-of-accounts", "/settings/dimensions", "/accounting/vendors", "/accounting/customers", "/inventory/warehouses", "/accounting/items", "/accounting/tax-codes", "/accounting/posting-profiles", "/settings/integrations"],
      settings: ["/settings/company", "/users", "/settings/account", "/settings/audit"],
      admin: ["/admin"],
    };

    for (const [section, prefixes] of Object.entries(sectionForPath)) {
      if (prefixes.some((prefix) => path === prefix || path.startsWith(prefix + "/"))) {
        setExpanded((prev) => {
          if (prev[section]) return prev; // already open
          return { ...prev, [section]: true };
        });
        break;
      }
    }
  }, [router.pathname]);

  // Ref for preserving sidebar scroll position
  const navRef = useRef<HTMLElement>(null);
  const SCROLL_KEY = "nxentra-sidebar-scroll";

  // Restore scroll position on mount and after route changes
  useEffect(() => {
    const restoreScroll = () => {
      try {
        const saved = sessionStorage.getItem(SCROLL_KEY);
        if (saved && navRef.current) {
          navRef.current.scrollTop = parseInt(saved, 10);
        }
      } catch {
        // Ignore sessionStorage errors
      }
    };

    const handleRouteChangeStart = () => {
      try {
        if (navRef.current) {
          sessionStorage.setItem(SCROLL_KEY, String(navRef.current.scrollTop));
        }
      } catch {
        // Ignore sessionStorage errors
      }
    };

    const handleRouteChangeComplete = () => {
      close(); // Close mobile sidebar
      // Restore scroll position after DOM settles
      requestAnimationFrame(() => {
        restoreScroll();
      });
    };

    // Restore on initial mount
    restoreScroll();

    router.events.on("routeChangeStart", handleRouteChangeStart);
    router.events.on("routeChangeComplete", handleRouteChangeComplete);

    return () => {
      router.events.off("routeChangeStart", handleRouteChangeStart);
      router.events.off("routeChangeComplete", handleRouteChangeComplete);
    };
  }, [router.events, close]);

  const isAdmin = user?.is_staff || user?.is_superuser;

  const navItems: NavItem[] = [
    {
      label: t("nav.dashboard"),
      href: "/dashboard",
      icon: <LayoutDashboard className="h-5 w-5 text-blue-500" />,
    },
    {
      label: t("nav.setup", "Setup"),
      icon: <Wrench className="h-5 w-5 text-cyan-500" />,
      children: [
        {
          label: t("nav.periods"),
          href: "/settings/periods",
          icon: <Calendar className="h-4 w-4 text-purple-400" />,
        },
        {
          label: t("nav.chartOfAccounts"),
          href: "/accounting/chart-of-accounts",
          icon: <FileText className="h-4 w-4 text-emerald-400" />,
        },
        {
          label: t("nav.dimensions"),
          href: "/settings/dimensions",
          icon: <Layers className="h-4 w-4 text-cyan-400" />,
        },
        {
          label: t("nav.vendors", "Vendors (AP)"),
          href: "/accounting/vendors",
          icon: <Truck className="h-4 w-4 text-amber-500" />,
        },
        {
          label: t("nav.customers", "Customers (AR)"),
          href: "/accounting/customers",
          icon: <UserCircle className="h-4 w-4 text-sky-400" />,
        },
        {
          label: t("nav.warehouses", "Warehouses"),
          href: "/inventory/warehouses",
          icon: <Warehouse className="h-4 w-4 text-cyan-400" />,
        },
        {
          label: t("nav.items", "Items"),
          href: "/accounting/items",
          icon: <Package className="h-4 w-4 text-amber-400" />,
        },
        {
          label: t("nav.taxCodes", "Tax Codes"),
          href: "/accounting/tax-codes",
          icon: <Percent className="h-4 w-4 text-rose-400" />,
        },
        {
          label: t("nav.postingProfiles", "Posting Profiles"),
          href: "/accounting/posting-profiles",
          icon: <CreditCard className="h-4 w-4 text-fuchsia-400" />,
        },
        {
          label: t("nav.integrations", "Integrations"),
          href: "/settings/integrations",
          icon: <Plug className="h-4 w-4 text-green-400" />,
        },
      ],
    },
    {
      label: t("nav.accounting"),
      icon: <BookOpen className="h-5 w-5 text-emerald-500" />,
      children: [
        {
          label: t("nav.journalEntries"),
          href: "/accounting/journal-entries",
          icon: <FileText className="h-4 w-4 text-teal-400" />,
        },
        {
          label: t("nav.scratchpad", "Scratchpad"),
          href: "/accounting/scratchpad",
          icon: <ClipboardList className="h-4 w-4 text-lime-500" />,
        },
        {
          label: t("nav.import", "Import Data"),
          href: "/accounting/import",
          icon: <Upload className="h-4 w-4 text-cyan-500" />,
        },
      ],
    },
    {
      label: t("nav.sales", "Sales"),
      icon: <ShoppingCart className="h-5 w-5 text-orange-500" />,
      children: [
        {
          label: t("nav.salesInvoices", "Invoices"),
          href: "/accounting/sales-invoices",
          icon: <Receipt className="h-4 w-4 text-orange-400" />,
        },
        {
          label: t("nav.customerReceipts", "Receipts"),
          href: "/accounting/receipts",
          icon: <CreditCard className="h-4 w-4 text-emerald-400" />,
        },
      ],
    },
    {
      label: t("nav.purchases", "Purchases"),
      icon: <Truck className="h-5 w-5 text-violet-500" />,
      children: [
        {
          label: t("nav.purchaseBills", "Bills"),
          href: "/accounting/purchase-bills",
          icon: <Receipt className="h-4 w-4 text-violet-400" />,
        },
        {
          label: t("nav.vendorPayments", "Payments"),
          href: "/accounting/payments",
          icon: <CreditCard className="h-4 w-4 text-violet-400" />,
        },
      ],
    },
    {
      label: t("nav.inventory", "Inventory"),
      icon: <Warehouse className="h-5 w-5 text-teal-500" />,
      children: [
        {
          label: t("nav.inventoryBalances", "Stock Balances"),
          href: "/inventory/balances",
          icon: <PackageOpen className="h-4 w-4 text-teal-400" />,
        },
        {
          label: t("nav.stockLedger", "Stock Ledger"),
          href: "/inventory/ledger",
          icon: <ScrollText className="h-4 w-4 text-emerald-400" />,
        },
        {
          label: t("nav.inventoryAdjustment", "Adjustment"),
          href: "/inventory/adjustments/new",
          icon: <Scale className="h-4 w-4 text-amber-400" />,
        },
        {
          label: t("nav.openingBalance", "Opening Balance"),
          href: "/inventory/opening-balance",
          icon: <PackagePlus className="h-4 w-4 text-lime-400" />,
        },
      ],
    },
    {
      label: t("nav.properties", "Properties"),
      icon: <Home className="h-5 w-5 text-amber-600" />,
      children: [
        {
          label: t("nav.propDashboard", "Dashboard"),
          href: "/properties/dashboard",
          icon: <LayoutGrid className="h-4 w-4 text-amber-600" />,
        },
        {
          label: t("nav.propertiesList", "Properties"),
          href: "/properties/properties",
          icon: <Building2 className="h-4 w-4 text-amber-500" />,
        },
        {
          label: t("nav.units", "Units"),
          href: "/properties/units",
          icon: <DoorOpen className="h-4 w-4 text-amber-400" />,
        },
        {
          label: t("nav.lessees", "Lessees"),
          href: "/properties/lessees",
          icon: <UserSquare2 className="h-4 w-4 text-orange-400" />,
        },
        {
          label: t("nav.leases", "Leases"),
          href: "/properties/leases",
          icon: <FileSignature className="h-4 w-4 text-orange-500" />,
        },
        {
          label: t("nav.collections", "Collections"),
          href: "/properties/payments",
          icon: <Banknote className="h-4 w-4 text-green-500" />,
        },
        {
          label: t("nav.propExpenses", "Expenses"),
          href: "/properties/expenses",
          icon: <Receipt className="h-4 w-4 text-red-400" />,
        },
        {
          label: t("nav.propAlerts", "Alerts"),
          href: "/properties/alerts",
          icon: <AlertTriangle className="h-4 w-4 text-yellow-500" />,
        },
        {
          label: t("nav.propReports", "Reports"),
          href: "/properties/reports",
          icon: <PieChart className="h-4 w-4 text-pink-400" />,
        },
      ],
    },
    {
      label: t("nav.clinic", "Clinic"),
      icon: <Stethoscope className="h-5 w-5 text-teal-600" />,
      children: [
        {
          label: t("nav.patients", "Patients"),
          href: "/clinic/patients",
          icon: <HeartPulse className="h-4 w-4 text-teal-500" />,
        },
        {
          label: t("nav.doctors", "Doctors"),
          href: "/clinic/doctors",
          icon: <Stethoscope className="h-4 w-4 text-teal-400" />,
        },
        {
          label: t("nav.visits", "Visits"),
          href: "/clinic/visits",
          icon: <CalendarCheck className="h-4 w-4 text-cyan-500" />,
        },
        {
          label: t("nav.clinicInvoices", "Invoices"),
          href: "/clinic/invoices",
          icon: <ClipboardCheck className="h-4 w-4 text-blue-500" />,
        },
        {
          label: t("nav.clinicPayments", "Payments"),
          href: "/clinic/payments",
          icon: <Banknote className="h-4 w-4 text-green-500" />,
        },
        {
          label: t("nav.clinicSettings", "Settings"),
          href: "/clinic/settings",
          icon: <Settings className="h-4 w-4 text-gray-500" />,
        },
      ],
    },
    {
      label: t("nav.reports"),
      icon: <BarChart3 className="h-5 w-5 text-pink-500" />,
      children: [
        {
          label: t("nav.trialBalance"),
          href: "/reports/trial-balance",
          icon: <BarChart3 className="h-4 w-4 text-pink-400" />,
        },
        {
          label: t("nav.balanceSheet"),
          href: "/reports/balance-sheet",
          icon: <BarChart3 className="h-4 w-4 text-rose-400" />,
        },
        {
          label: t("nav.incomeStatement"),
          href: "/reports/income-statement",
          icon: <BarChart3 className="h-4 w-4 text-red-400" />,
        },
        {
          label: t("nav.cashFlowStatement", "Cash Flow"),
          href: "/reports/cash-flow",
          icon: <BarChart3 className="h-4 w-4 text-cyan-400" />,
        },
        {
          label: t("nav.accountInquiry", "Account Inquiry"),
          href: "/reports/account-inquiry",
          icon: <FileText className="h-4 w-4 text-purple-400" />,
        },
        {
          label: t("nav.customerBalances", "Customer Balances"),
          href: "/reports/customer-balances",
          icon: <UserCircle className="h-4 w-4 text-sky-400" />,
        },
        {
          label: t("nav.vendorBalances", "Vendor Balances"),
          href: "/reports/vendor-balances",
          icon: <Truck className="h-4 w-4 text-amber-400" />,
        },
        {
          label: t("nav.customerStatement", "Customer Statement"),
          href: "/reports/customer-statement",
          icon: <UserCircle className="h-4 w-4 text-emerald-400" />,
        },
        {
          label: t("nav.vendorStatement", "Vendor Statement"),
          href: "/reports/vendor-statement",
          icon: <Truck className="h-4 w-4 text-orange-400" />,
        },
      ],
    },
    {
      label: t("nav.settings"),
      icon: <Settings className="h-5 w-5 text-slate-500" />,
      children: [
        {
          label: t("nav.companySettings"),
          href: "/settings/company",
          icon: <Building2 className="h-4 w-4 text-slate-400" />,
        },
        {
          label: t("nav.users"),
          href: "/users",
          icon: <Users className="h-4 w-4 text-indigo-400" />,
        },
        {
          label: t("nav.account", "Account"),
          href: "/settings/account",
          icon: <KeyRound className="h-4 w-4 text-yellow-500" />,
        },
        {
          label: t("nav.audit", "Event Audit"),
          href: "/settings/audit",
          icon: <ShieldCheck className="h-4 w-4 text-blue-400" />,
        },
      ],
    },
  ];

  // Add admin section only for staff/superusers
  if (isAdmin) {
    const adminChildren: NavItem[] = [
      {
        label: t("nav.pendingUsers", "Pending Users"),
        href: "/admin/pending-users",
        icon: <UserCheck className="h-4 w-4 text-amber-400" />,
      },
      {
        label: t("nav.projections", "Projections"),
        href: "/admin/projections",
        icon: <Database className="h-4 w-4 text-cyan-400" />,
      },
      {
        label: t("nav.voiceSettings", "Voice Settings"),
        href: "/settings/voice",
        icon: <Mic className="h-4 w-4 text-violet-400" />,
      },
    ];

    // Add superuser-only admin pages
    if (user?.is_superuser) {
      adminChildren.unshift(
        {
          label: t("nav.adminDashboard", "Dashboard"),
          href: "/admin",
          icon: <ShieldCheck className="h-4 w-4 text-red-400" />,
        },
        {
          label: t("nav.allCompanies", "All Companies"),
          href: "/admin/companies",
          icon: <Building2 className="h-4 w-4 text-purple-400" />,
        },
        {
          label: t("nav.allUsers", "All Users"),
          href: "/admin/all-users",
          icon: <Users className="h-4 w-4 text-indigo-400" />,
        },
        {
          label: t("nav.auditLog", "Audit Log"),
          href: "/admin/audit-log",
          icon: <FileText className="h-4 w-4 text-emerald-400" />,
        }
      );
    }

    navItems.push({
      label: t("nav.admin", "Admin"),
      icon: <ShieldCheck className="h-5 w-5 text-red-500" />,
      children: adminChildren,
    });
  }

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
          {/* Close button for mobile */}
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
          <div className="space-y-1">{navItems.map((item) => renderNavItem(item))}</div>
        </nav>
      </aside>
    </>
  );
}
