import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { AppLayout } from "@/components/layout";
import { PageHeader } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { useToast } from "@/components/ui/toaster";
import { useCreateLessee } from "@/queries/useProperties";
import type { LesseeType } from "@/types/properties";
import { useState } from "react";

export default function NewLesseePage() {
  const router = useRouter();
  const { toast } = useToast();
  const createLessee = useCreateLessee();

  const [form, setForm] = useState({
    code: "",
    lessee_type: "individual" as LesseeType,
    display_name: "",
    display_name_ar: "",
    national_id: "",
    phone: "",
    whatsapp: "",
    email: "",
    address: "",
    emergency_contact: "",
    notes: "",
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createLessee.mutateAsync({
        code: form.code,
        lessee_type: form.lessee_type,
        display_name: form.display_name,
        display_name_ar: form.display_name_ar || undefined,
        national_id: form.national_id || null,
        phone: form.phone || null,
        whatsapp: form.whatsapp || null,
        email: form.email || null,
        address: form.address || null,
        emergency_contact: form.emergency_contact || null,
        notes: form.notes,
      });
      toast({ title: "Lessee created", description: `${form.display_name} has been created.` });
      router.push("/properties/lessees");
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.detail || "Failed to create lessee.",
        variant: "destructive",
      });
    }
  };

  return (
    <AppLayout>
      <div className="max-w-2xl space-y-6">
        <PageHeader title="New Lessee" subtitle="Add a new property tenant" />

        <Card>
          <CardContent className="p-6">
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-sm font-medium">Code *</label>
                  <Input
                    value={form.code}
                    onChange={(e) => setForm({ ...form, code: e.target.value })}
                    placeholder="e.g. LSE001"
                    required
                  />
                </div>
                <div>
                  <label className="text-sm font-medium">Type *</label>
                  <select
                    value={form.lessee_type}
                    onChange={(e) => setForm({ ...form, lessee_type: e.target.value as LesseeType })}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value="individual">Individual</option>
                    <option value="company">Company</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="text-sm font-medium">Display Name *</label>
                <Input
                  value={form.display_name}
                  onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                  placeholder="Full name or company name"
                  required
                />
              </div>

              <div>
                <label className="text-sm font-medium">Display Name (Arabic)</label>
                <Input
                  value={form.display_name_ar}
                  onChange={(e) => setForm({ ...form, display_name_ar: e.target.value })}
                  placeholder="الاسم بالعربي"
                  dir="rtl"
                />
              </div>

              <div>
                <label className="text-sm font-medium">National ID / CR Number</label>
                <Input
                  value={form.national_id}
                  onChange={(e) => setForm({ ...form, national_id: e.target.value })}
                  placeholder="ID or commercial registration"
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-sm font-medium">Phone</label>
                  <Input
                    value={form.phone}
                    onChange={(e) => setForm({ ...form, phone: e.target.value })}
                    placeholder="+966..."
                  />
                </div>
                <div>
                  <label className="text-sm font-medium">WhatsApp</label>
                  <Input
                    value={form.whatsapp}
                    onChange={(e) => setForm({ ...form, whatsapp: e.target.value })}
                    placeholder="+966..."
                  />
                </div>
              </div>

              <div>
                <label className="text-sm font-medium">Email</label>
                <Input
                  type="email"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  placeholder="email@example.com"
                />
              </div>

              <div>
                <label className="text-sm font-medium">Address</label>
                <Input
                  value={form.address}
                  onChange={(e) => setForm({ ...form, address: e.target.value })}
                  placeholder="Full address"
                />
              </div>

              <div>
                <label className="text-sm font-medium">Emergency Contact</label>
                <Input
                  value={form.emergency_contact}
                  onChange={(e) => setForm({ ...form, emergency_contact: e.target.value })}
                  placeholder="Name and phone"
                />
              </div>

              <div>
                <label className="text-sm font-medium">Notes</label>
                <textarea
                  value={form.notes}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })}
                  className="flex min-h-[60px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                />
              </div>

              <div className="flex gap-3 pt-2">
                <Button type="submit" disabled={createLessee.isPending}>
                  {createLessee.isPending ? "Creating..." : "Create Lessee"}
                </Button>
                <Button type="button" variant="outline" onClick={() => router.back()}>
                  Cancel
                </Button>
              </div>
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
