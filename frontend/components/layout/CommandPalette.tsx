import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/router";
import { Command } from "cmdk";
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
  Search,
} from "lucide-react";
import { useSidebarNav, type SidebarSection } from "@/queries/useModules";
import { Dialog, DialogContent } from "@/components/ui/dialog";

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

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const { data: sections } = useSidebarNav();

  // Cmd+K / Ctrl+K to open
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    document.addEventListener("keydown", down);
    return () => document.removeEventListener("keydown", down);
  }, []);

  const handleSelect = useCallback(
    (href: string) => {
      setOpen(false);
      router.push(href);
    },
    [router]
  );

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="overflow-hidden p-0 max-w-lg">
        <Command className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-muted-foreground">
          <div className="flex items-center border-b px-3">
            <Search className="mr-2 h-4 w-4 shrink-0 opacity-50" />
            <Command.Input
              placeholder="Search pages..."
              className="flex h-11 w-full rounded-md bg-transparent py-3 text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>
          <Command.List className="max-h-[300px] overflow-y-auto overflow-x-hidden p-1">
            <Command.Empty className="py-6 text-center text-sm text-muted-foreground">
              No results found.
            </Command.Empty>
            {sections?.map((section: SidebarSection) => (
              <Command.Group key={section.key} heading={section.label}>
                {section.nav_items.map((item) => {
                  const Icon = ICON_MAP[item.icon] || FileText;
                  return (
                    <Command.Item
                      key={item.href}
                      value={`${section.label} ${item.label}`}
                      onSelect={() => handleSelect(item.href)}
                      className="relative flex cursor-pointer select-none items-center rounded-sm px-2 py-1.5 text-sm outline-none aria-selected:bg-accent aria-selected:text-accent-foreground data-[disabled]:pointer-events-none data-[disabled]:opacity-50"
                    >
                      <Icon className="mr-2 h-4 w-4 opacity-60" />
                      {item.label}
                    </Command.Item>
                  );
                })}
              </Command.Group>
            ))}
          </Command.List>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
