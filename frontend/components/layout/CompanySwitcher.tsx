import { useEffect, useState } from "react";
import { useRouter } from "next/router";
import { useTranslation } from "next-i18next";
import { Building2, ChevronDown, Check, Plus } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { companyService } from "@/services/company.service";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";

interface CompanyItem {
  id: number;
  public_id: string;
  name: string;
  role?: string;
}

export function CompanySwitcher() {
  const { t } = useTranslation("common");
  const router = useRouter();
  const { company, switchCompany, membership } = useAuth();
  const [companies, setCompanies] = useState<CompanyItem[]>([]);
  const [switching, setSwitching] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    companyService.list().then((res) => {
      setCompanies(res.data);
    }).catch(() => {});
  }, []);

  const handleSwitch = async (companyId: number) => {
    if (companyId === company?.id || switching) return;
    setSwitching(true);
    try {
      await switchCompany(companyId);
      router.reload();
    } catch {
      setSwitching(false);
    }
  };

  const handleCreate = async () => {
    if (!newName.trim() || creating) return;
    setCreating(true);
    try {
      await companyService.create({ name: newName.trim() });
      setCreateOpen(false);
      setNewName("");
      router.reload();
    } catch {
      setCreating(false);
    }
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" className="flex items-center gap-2 px-2" disabled={switching}>
            <Building2 className="h-5 w-5 text-muted-foreground" />
            <span className="text-lg font-semibold">{company?.name || "Nxentra"}</span>
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56">
          {companies.map((c) => (
            <DropdownMenuItem
              key={c.id}
              onClick={() => handleSwitch(c.id)}
              className={c.id === company?.id ? "bg-muted" : ""}
            >
              <Building2 className="h-4 w-4 me-2 text-muted-foreground" />
              <span className="flex-1">{c.name}</span>
              {c.id === company?.id && <Check className="h-4 w-4 ms-2" />}
            </DropdownMenuItem>
          ))}
          {membership?.role === "OWNER" && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => setCreateOpen(true)}>
                <Plus className="h-4 w-4 me-2" />
                <span>{t("nav.createCompany", "Create Company")}</span>
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("nav.createCompany", "Create Company")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="company-name">{t("nav.companyName", "Company Name")}</Label>
              <Input
                id="company-name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                placeholder={t("nav.companyNamePlaceholder", "Enter company name")}
                autoFocus
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              {t("actions.cancel")}
            </Button>
            <Button onClick={handleCreate} disabled={!newName.trim() || creating}>
              {creating ? t("actions.loading") : t("actions.create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
