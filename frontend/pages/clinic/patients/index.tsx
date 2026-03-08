import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { Plus, Search, HeartPulse } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import { usePatients, useCreatePatient } from "@/queries/useClinic";
import type { Patient, PatientCreatePayload } from "@/types/clinic";
import { cn } from "@/lib/cn";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/toaster";

const STATUS_COLORS: Record<string, string> = {
  active: "bg-green-100 text-green-800",
  inactive: "bg-gray-100 text-gray-800",
};

export default function PatientsPage() {
  const { t } = useTranslation(["common"]);
  const router = useRouter();
  const { data: patients, isLoading } = usePatients();
  const createPatient = useCreatePatient();
  const { toast } = useToast();
  const [search, setSearch] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<PatientCreatePayload>({ code: "", name: "", phone: "", gender: "" });

  const filtered = patients?.filter((p) => {
    if (!search) return true;
    const s = search.toLowerCase();
    return (
      p.code.toLowerCase().includes(s) ||
      p.name.toLowerCase().includes(s) ||
      p.phone.toLowerCase().includes(s)
    );
  });

  const handleCreate = async () => {
    try {
      await createPatient.mutateAsync(form);
      toast({ title: "Patient created" });
      setShowCreate(false);
      setForm({ code: "", name: "", phone: "", gender: "" });
    } catch (e: any) {
      toast({ title: e?.response?.data?.detail || "Failed to create patient", variant: "destructive" });
    }
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Patients"
          subtitle="Manage patient records"
          actions={
            <Button onClick={() => setShowCreate(true)}>
              <Plus className="mr-2 h-4 w-4" />
              Add Patient
            </Button>
          }
        />

        <div className="flex items-center gap-4">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Search patients..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9"
            />
          </div>
        </div>

        {isLoading ? (
          <LoadingSpinner />
        ) : !filtered?.length ? (
          <EmptyState
            icon={<HeartPulse className="h-12 w-12 text-muted-foreground" />}
            title="No patients found"
            description="Add your first patient to get started."
          />
        ) : (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {filtered.map((patient) => (
              <Card
                key={patient.id}
                className="cursor-pointer hover:shadow-md transition-shadow"
                onClick={() => router.push(`/clinic/patients/${patient.id}`)}
              >
                <CardContent className="p-4">
                  <div className="flex items-start justify-between">
                    <div>
                      <p className="font-semibold">{patient.name}</p>
                      <p className="text-sm text-muted-foreground">{patient.code}</p>
                    </div>
                    <Badge className={cn(STATUS_COLORS[patient.status])}>
                      {patient.status}
                    </Badge>
                  </div>
                  <div className="mt-3 text-sm text-muted-foreground space-y-1">
                    {patient.phone && <p>Phone: {patient.phone}</p>}
                    {patient.gender && <p>Gender: {patient.gender}</p>}
                    <p>Visits: {patient.visit_count}</p>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New Patient</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>Code *</Label>
              <Input value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="P001" />
            </div>
            <div>
              <Label>Name *</Label>
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Patient name" />
            </div>
            <div>
              <Label>Phone</Label>
              <Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} placeholder="05XXXXXXXX" />
            </div>
            <div>
              <Label>Gender</Label>
              <select
                className="w-full border rounded-md px-3 py-2 text-sm"
                value={form.gender}
                onChange={(e) => setForm({ ...form, gender: e.target.value as PatientCreatePayload["gender"] })}
              >
                <option value="">Select...</option>
                <option value="male">Male</option>
                <option value="female">Female</option>
              </select>
            </div>
            <Button
              className="w-full"
              onClick={handleCreate}
              disabled={!form.code || !form.name || createPatient.isPending}
            >
              {createPatient.isPending ? "Creating..." : "Create Patient"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
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
