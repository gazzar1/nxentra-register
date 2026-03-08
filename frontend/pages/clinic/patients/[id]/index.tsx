import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { ArrowLeft, Pencil, Save } from "lucide-react";
import { useState, useEffect } from "react";
import Link from "next/link";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadingSpinner } from "@/components/common";
import { usePatient, useUpdatePatient } from "@/queries/useClinic";
import { useToast } from "@/components/ui/toaster";

export default function PatientDetailPage() {
  const router = useRouter();
  const id = Number(router.query.id);
  const { data: patient, isLoading } = usePatient(id);
  const updatePatient = useUpdatePatient();
  const { toast } = useToast();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Record<string, any>>({});

  useEffect(() => {
    if (patient) {
      setForm({
        name: patient.name,
        phone: patient.phone,
        email: patient.email,
        national_id: patient.national_id,
        blood_type: patient.blood_type,
        allergies: patient.allergies,
        chronic_diseases: patient.chronic_diseases,
        current_medications: patient.current_medications,
        emergency_contact_name: patient.emergency_contact_name,
        emergency_contact_phone: patient.emergency_contact_phone,
        notes: patient.notes,
      });
    }
  }, [patient]);

  const handleSave = async () => {
    try {
      await updatePatient.mutateAsync({ id, data: form });
      toast({ title: "Patient updated" });
      setEditing(false);
    } catch (e: any) {
      toast({ title: e?.response?.data?.detail || "Update failed", variant: "destructive" });
    }
  };

  if (isLoading) return <AppLayout><LoadingSpinner /></AppLayout>;
  if (!patient) return <AppLayout><p>Patient not found.</p></AppLayout>;

  return (
    <AppLayout>
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Link href="/clinic/patients">
            <Button variant="ghost" size="icon"><ArrowLeft className="h-4 w-4" /></Button>
          </Link>
          <div className="flex-1">
            <h1 className="text-2xl font-bold">{patient.name}</h1>
            <p className="text-muted-foreground">{patient.code}</p>
          </div>
          <Badge>{patient.status}</Badge>
          {editing ? (
            <Button onClick={handleSave} disabled={updatePatient.isPending}>
              <Save className="mr-2 h-4 w-4" />Save
            </Button>
          ) : (
            <Button variant="outline" onClick={() => setEditing(true)}>
              <Pencil className="mr-2 h-4 w-4" />Edit
            </Button>
          )}
        </div>

        <div className="grid gap-6 md:grid-cols-2">
          <Card>
            <CardHeader><CardTitle>Personal Info</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <Field label="Name" value={form.name} editing={editing} onChange={(v) => setForm({ ...form, name: v })} />
              <Field label="Phone" value={form.phone} editing={editing} onChange={(v) => setForm({ ...form, phone: v })} />
              <Field label="Email" value={form.email} editing={editing} onChange={(v) => setForm({ ...form, email: v })} />
              <Field label="National ID" value={form.national_id} editing={editing} onChange={(v) => setForm({ ...form, national_id: v })} />
              <div className="text-sm"><span className="text-muted-foreground">Gender:</span> {patient.gender || "—"}</div>
              <div className="text-sm"><span className="text-muted-foreground">DOB:</span> {patient.date_of_birth || "—"}</div>
              <div className="text-sm"><span className="text-muted-foreground">Blood Type:</span> {patient.blood_type || "—"}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Medical Info</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <ListField label="Allergies" items={form.allergies || []} editing={editing} onChange={(v) => setForm({ ...form, allergies: v })} />
              <ListField label="Chronic Diseases" items={form.chronic_diseases || []} editing={editing} onChange={(v) => setForm({ ...form, chronic_diseases: v })} />
              <ListField label="Current Medications" items={form.current_medications || []} editing={editing} onChange={(v) => setForm({ ...form, current_medications: v })} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Emergency Contact</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <Field label="Name" value={form.emergency_contact_name} editing={editing} onChange={(v) => setForm({ ...form, emergency_contact_name: v })} />
              <Field label="Phone" value={form.emergency_contact_phone} editing={editing} onChange={(v) => setForm({ ...form, emergency_contact_phone: v })} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Notes</CardTitle></CardHeader>
            <CardContent>
              {editing ? (
                <textarea
                  className="w-full border rounded-md px-3 py-2 text-sm min-h-[100px]"
                  value={form.notes || ""}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })}
                />
              ) : (
                <p className="text-sm whitespace-pre-wrap">{patient.notes || "No notes."}</p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </AppLayout>
  );
}

function Field({ label, value, editing, onChange }: { label: string; value: string; editing: boolean; onChange: (v: string) => void }) {
  if (editing) {
    return (
      <div>
        <Label className="text-xs text-muted-foreground">{label}</Label>
        <Input value={value || ""} onChange={(e) => onChange(e.target.value)} />
      </div>
    );
  }
  return (
    <div className="text-sm">
      <span className="text-muted-foreground">{label}:</span> {value || "—"}
    </div>
  );
}

function ListField({ label, items, editing, onChange }: { label: string; items: string[]; editing: boolean; onChange: (v: string[]) => void }) {
  if (editing) {
    return (
      <div>
        <Label className="text-xs text-muted-foreground">{label} (comma-separated)</Label>
        <Input
          value={items.join(", ")}
          onChange={(e) => onChange(e.target.value.split(",").map((s) => s.trim()).filter(Boolean))}
        />
      </div>
    );
  }
  return (
    <div className="text-sm">
      <span className="text-muted-foreground">{label}:</span>{" "}
      {items.length ? items.join(", ") : "None"}
    </div>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
