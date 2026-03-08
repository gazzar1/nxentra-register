import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { Plus, CalendarCheck, CheckCircle } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import { useVisits, useCreateVisit, useCompleteVisit, usePatients, useDoctors } from "@/queries/useClinic";
import { cn } from "@/lib/cn";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toaster";

const STATUS_COLORS: Record<string, string> = {
  scheduled: "bg-blue-100 text-blue-800",
  in_progress: "bg-yellow-100 text-yellow-800",
  completed: "bg-green-100 text-green-800",
  cancelled: "bg-red-100 text-red-800",
};

export default function VisitsPage() {
  const { data: visits, isLoading } = useVisits();
  const { data: patients } = usePatients();
  const { data: doctors } = useDoctors();
  const createVisit = useCreateVisit();
  const completeVisit = useCompleteVisit();
  const { toast } = useToast();
  const [showCreate, setShowCreate] = useState(false);
  const [showComplete, setShowComplete] = useState<number | null>(null);
  const [form, setForm] = useState({
    patient_id: "",
    doctor_id: "",
    visit_date: new Date().toISOString().split("T")[0],
    visit_type: "consultation",
    chief_complaint: "",
  });
  const [diagnosis, setDiagnosis] = useState("");

  const handleCreate = async () => {
    try {
      await createVisit.mutateAsync({
        ...form,
        patient_id: Number(form.patient_id),
        doctor_id: Number(form.doctor_id),
      } as any);
      toast({ title: "Visit created" });
      setShowCreate(false);
      setForm({ patient_id: "", doctor_id: "", visit_date: new Date().toISOString().split("T")[0], visit_type: "consultation", chief_complaint: "" });
    } catch (e: any) {
      toast({ title: e?.response?.data?.detail || "Failed to create visit", variant: "destructive" });
    }
  };

  const handleComplete = async () => {
    if (!showComplete) return;
    try {
      await completeVisit.mutateAsync({ id: showComplete, data: { diagnosis } });
      toast({ title: "Visit completed" });
      setShowComplete(null);
      setDiagnosis("");
    } catch (e: any) {
      toast({ title: e?.response?.data?.detail || "Failed to complete visit", variant: "destructive" });
    }
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Visits"
          subtitle="Track patient visits"
          actions={
            <Button onClick={() => setShowCreate(true)}>
              <Plus className="mr-2 h-4 w-4" />
              New Visit
            </Button>
          }
        />

        {isLoading ? (
          <LoadingSpinner />
        ) : !visits?.length ? (
          <EmptyState
            icon={<CalendarCheck className="h-12 w-12 text-muted-foreground" />}
            title="No visits found"
            description="Create a visit to get started."
          />
        ) : (
          <div className="space-y-3">
            {visits.map((visit) => (
              <Card key={visit.id}>
                <CardContent className="p-4 flex items-center justify-between">
                  <div>
                    <p className="font-semibold">{visit.patient_name} <span className="text-muted-foreground font-normal text-sm">({visit.patient_code})</span></p>
                    <p className="text-sm text-muted-foreground">
                      Dr. {visit.doctor_name} &middot; {visit.visit_date} &middot; {visit.visit_type}
                    </p>
                    {visit.chief_complaint && (
                      <p className="text-sm mt-1">Complaint: {visit.chief_complaint}</p>
                    )}
                    {visit.diagnosis && (
                      <p className="text-sm mt-1">Diagnosis: {visit.diagnosis}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge className={cn(STATUS_COLORS[visit.status])}>{visit.status}</Badge>
                    {visit.status === "scheduled" && (
                      <Button size="sm" variant="outline" onClick={() => setShowComplete(visit.id)}>
                        <CheckCircle className="mr-1 h-3 w-3" />Complete
                      </Button>
                    )}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      {/* Create Visit Dialog */}
      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent>
          <DialogHeader><DialogTitle>New Visit</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>Patient *</Label>
              <select className="w-full border rounded-md px-3 py-2 text-sm" value={form.patient_id} onChange={(e) => setForm({ ...form, patient_id: e.target.value })}>
                <option value="">Select patient...</option>
                {patients?.map((p) => <option key={p.id} value={p.id}>{p.code} - {p.name}</option>)}
              </select>
            </div>
            <div>
              <Label>Doctor *</Label>
              <select className="w-full border rounded-md px-3 py-2 text-sm" value={form.doctor_id} onChange={(e) => setForm({ ...form, doctor_id: e.target.value })}>
                <option value="">Select doctor...</option>
                {doctors?.map((d) => <option key={d.id} value={d.id}>{d.code} - {d.name}</option>)}
              </select>
            </div>
            <div>
              <Label>Date *</Label>
              <Input type="date" value={form.visit_date} onChange={(e) => setForm({ ...form, visit_date: e.target.value })} />
            </div>
            <div>
              <Label>Type</Label>
              <select className="w-full border rounded-md px-3 py-2 text-sm" value={form.visit_type} onChange={(e) => setForm({ ...form, visit_type: e.target.value })}>
                <option value="consultation">Consultation</option>
                <option value="follow_up">Follow-up</option>
                <option value="procedure">Procedure</option>
                <option value="emergency">Emergency</option>
              </select>
            </div>
            <div>
              <Label>Chief Complaint</Label>
              <Input value={form.chief_complaint} onChange={(e) => setForm({ ...form, chief_complaint: e.target.value })} />
            </div>
            <Button className="w-full" onClick={handleCreate} disabled={!form.patient_id || !form.doctor_id || createVisit.isPending}>
              {createVisit.isPending ? "Creating..." : "Create Visit"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Complete Visit Dialog */}
      <Dialog open={!!showComplete} onOpenChange={() => setShowComplete(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>Complete Visit</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>Diagnosis</Label>
              <textarea className="w-full border rounded-md px-3 py-2 text-sm min-h-[80px]" value={diagnosis} onChange={(e) => setDiagnosis(e.target.value)} />
            </div>
            <Button className="w-full" onClick={handleComplete} disabled={completeVisit.isPending}>
              {completeVisit.isPending ? "Completing..." : "Mark Complete"}
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
