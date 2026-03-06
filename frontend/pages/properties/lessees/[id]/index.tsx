import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { AppLayout } from "@/components/layout";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/components/ui/toaster";
import { useLessee, useUpdateLessee } from "@/queries/useProperties";
import type { LesseeType, LesseeStatus, RiskRating } from "@/types/properties";
import { useState, useEffect } from "react";

export default function LesseeDetailPage() {
  const router = useRouter();
  const { toast } = useToast();
  const id = Number(router.query.id);
  const { data: lessee, isLoading } = useLessee(id);
  const updateLessee = useUpdateLessee();

  const [form, setForm] = useState({
    lessee_type: "individual" as LesseeType,
    display_name: "",
    display_name_ar: "",
    national_id: "",
    phone: "",
    whatsapp: "",
    email: "",
    address: "",
    emergency_contact: "",
    status: "active" as LesseeStatus,
    risk_rating: "" as string,
    notes: "",
  });

  useEffect(() => {
    if (lessee) {
      setForm({
        lessee_type: lessee.lessee_type,
        display_name: lessee.display_name,
        display_name_ar: lessee.display_name_ar,
        national_id: lessee.national_id || "",
        phone: lessee.phone || "",
        whatsapp: lessee.whatsapp || "",
        email: lessee.email || "",
        address: lessee.address || "",
        emergency_contact: lessee.emergency_contact || "",
        status: lessee.status,
        risk_rating: lessee.risk_rating || "",
        notes: lessee.notes,
      });
    }
  }, [lessee]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await updateLessee.mutateAsync({
        id,
        lessee_type: form.lessee_type,
        display_name: form.display_name,
        display_name_ar: form.display_name_ar,
        national_id: form.national_id || null,
        phone: form.phone || null,
        whatsapp: form.whatsapp || null,
        email: form.email || null,
        address: form.address || null,
        emergency_contact: form.emergency_contact || null,
        status: form.status,
        risk_rating: (form.risk_rating || null) as RiskRating | null,
        notes: form.notes,
      });
      toast({ title: "Lessee updated" });
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.detail || "Failed to update lessee.",
        variant: "destructive",
      });
    }
  };

  if (isLoading) return <AppLayout><LoadingSpinner /></AppLayout>;
  if (!lessee) return <AppLayout><div>Lessee not found</div></AppLayout>;

  return (
    <AppLayout>
      <div className="max-w-2xl space-y-6">
        <PageHeader
          title={`${lessee.code} - ${lessee.display_name}`}
          subtitle="Lessee details"
        />

        <Card>
          <CardHeader>
            <CardTitle>Lessee Details</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-sm font-medium">Type</label>
                  <select
                    value={form.lessee_type}
                    onChange={(e) => setForm({ ...form, lessee_type: e.target.value as LesseeType })}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value="individual">Individual</option>
                    <option value="company">Company</option>
                  </select>
                </div>
                <div>
                  <label className="text-sm font-medium">Status</label>
                  <select
                    value={form.status}
                    onChange={(e) => setForm({ ...form, status: e.target.value as LesseeStatus })}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value="active">Active</option>
                    <option value="inactive">Inactive</option>
                    <option value="blacklisted">Blacklisted</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="text-sm font-medium">Display Name</label>
                <Input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} required />
              </div>

              <div>
                <label className="text-sm font-medium">Display Name (Arabic)</label>
                <Input value={form.display_name_ar} onChange={(e) => setForm({ ...form, display_name_ar: e.target.value })} dir="rtl" />
              </div>

              <div>
                <label className="text-sm font-medium">National ID / CR Number</label>
                <Input value={form.national_id} onChange={(e) => setForm({ ...form, national_id: e.target.value })} />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-sm font-medium">Phone</label>
                  <Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
                </div>
                <div>
                  <label className="text-sm font-medium">WhatsApp</label>
                  <Input value={form.whatsapp} onChange={(e) => setForm({ ...form, whatsapp: e.target.value })} />
                </div>
              </div>

              <div>
                <label className="text-sm font-medium">Email</label>
                <Input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
              </div>

              <div>
                <label className="text-sm font-medium">Address</label>
                <Input value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-sm font-medium">Emergency Contact</label>
                  <Input value={form.emergency_contact} onChange={(e) => setForm({ ...form, emergency_contact: e.target.value })} />
                </div>
                <div>
                  <label className="text-sm font-medium">Risk Rating</label>
                  <select
                    value={form.risk_rating}
                    onChange={(e) => setForm({ ...form, risk_rating: e.target.value })}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value="">Not set</option>
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="text-sm font-medium">Notes</label>
                <textarea
                  value={form.notes}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })}
                  className="flex min-h-[60px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                />
              </div>

              <Button type="submit" disabled={updateLessee.isPending}>
                {updateLessee.isPending ? "Saving..." : "Save Changes"}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
