import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { Plus, Stethoscope } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import { useDoctors, useCreateDoctor } from "@/queries/useClinic";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toaster";

export default function DoctorsPage() {
  const { data: doctors, isLoading } = useDoctors();
  const createDoctor = useCreateDoctor();
  const { toast } = useToast();
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ code: "", name: "", specialization: "", phone: "" });

  const handleCreate = async () => {
    try {
      await createDoctor.mutateAsync(form);
      toast({ title: "Doctor created" });
      setShowCreate(false);
      setForm({ code: "", name: "", specialization: "", phone: "" });
    } catch (e: any) {
      toast({ title: e?.response?.data?.detail || "Failed to create doctor", variant: "destructive" });
    }
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Doctors"
          subtitle="Manage clinic doctors"
          actions={
            <Button onClick={() => setShowCreate(true)}>
              <Plus className="mr-2 h-4 w-4" />
              Add Doctor
            </Button>
          }
        />

        {isLoading ? (
          <LoadingSpinner />
        ) : !doctors?.length ? (
          <EmptyState
            icon={<Stethoscope className="h-12 w-12 text-muted-foreground" />}
            title="No doctors found"
            description="Add your first doctor to get started."
          />
        ) : (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {doctors.map((doc) => (
              <Card key={doc.id}>
                <CardContent className="p-4">
                  <div className="flex items-start justify-between">
                    <div>
                      <p className="font-semibold">{doc.name}</p>
                      <p className="text-sm text-muted-foreground">{doc.code}</p>
                    </div>
                    <Badge variant={doc.is_active ? "default" : "secondary"}>
                      {doc.is_active ? "Active" : "Inactive"}
                    </Badge>
                  </div>
                  {doc.specialization && (
                    <p className="mt-2 text-sm text-muted-foreground">
                      {doc.specialization}
                    </p>
                  )}
                  {doc.phone && (
                    <p className="text-sm text-muted-foreground">{doc.phone}</p>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New Doctor</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>Code *</Label>
              <Input value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="D001" />
            </div>
            <div>
              <Label>Name *</Label>
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Dr. Name" />
            </div>
            <div>
              <Label>Specialization</Label>
              <Input value={form.specialization} onChange={(e) => setForm({ ...form, specialization: e.target.value })} placeholder="General" />
            </div>
            <div>
              <Label>Phone</Label>
              <Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
            </div>
            <Button className="w-full" onClick={handleCreate} disabled={!form.code || !form.name || createDoctor.isPending}>
              {createDoctor.isPending ? "Creating..." : "Create Doctor"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => ({
  props: { ...(await serverSideTranslations(locale ?? "en", ["common"])) },
});
