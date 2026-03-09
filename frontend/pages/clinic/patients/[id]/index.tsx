import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { ArrowLeft, Pencil, Save, Upload, FileText, Trash2 } from "lucide-react";
import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadingSpinner } from "@/components/common";
import { usePatient, useUpdatePatient, usePatientDocuments, useUploadDocument, useDeleteDocument } from "@/queries/useClinic";
import { clinicDocumentsService } from "@/services/clinic.service";
import { useToast } from "@/components/ui/toaster";
import type { PatientDocument, DocumentType } from "@/types/clinic";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const DOC_TYPE_LABELS: Record<string, string> = {
  prescription: "Prescription",
  lab_result: "Lab Result",
  radiology: "Radiology",
  surgery_report: "Surgery Report",
  referral: "Referral",
  other: "Other",
};

const DOC_TYPE_COLORS: Record<string, string> = {
  prescription: "bg-blue-100 text-blue-800",
  lab_result: "bg-purple-100 text-purple-800",
  radiology: "bg-indigo-100 text-indigo-800",
  surgery_report: "bg-red-100 text-red-800",
  referral: "bg-yellow-100 text-yellow-800",
  other: "bg-gray-100 text-gray-800",
};

function isImageMime(mime: string) {
  return mime.startsWith("image/");
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function PatientDetailPage() {
  const router = useRouter();
  const id = Number(router.query.id);
  const { data: patient, isLoading } = usePatient(id);
  const { data: documents, isLoading: docsLoading } = usePatientDocuments(id);
  const updatePatient = useUpdatePatient();
  const uploadDocument = useUploadDocument();
  const deleteDocument = useDeleteDocument();
  const { toast } = useToast();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Record<string, any>>({});
  const [showUpload, setShowUpload] = useState(false);
  const [uploadForm, setUploadForm] = useState({ title: "", document_type: "other" as DocumentType, notes: "" });
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (patient) {
      setForm({
        name: patient.name,
        phone: patient.phone,
        email: patient.email,
        national_id: patient.national_id,
        date_of_birth: patient.date_of_birth || "",
        gender: patient.gender || "",
        blood_type: patient.blood_type || "",
        allergies: (patient.allergies || []).join(", "),
        chronic_diseases: (patient.chronic_diseases || []).join(", "),
        current_medications: (patient.current_medications || []).join(", "),
        emergency_contact_name: patient.emergency_contact_name,
        emergency_contact_phone: patient.emergency_contact_phone,
        notes: patient.notes,
      });
    }
  }, [patient]);

  const parseList = (v: string) => v.split(",").map((s) => s.trim()).filter(Boolean);

  const handleSave = async () => {
    try {
      const payload: Record<string, any> = {
        ...form,
        allergies: parseList(form.allergies || ""),
        chronic_diseases: parseList(form.chronic_diseases || ""),
        current_medications: parseList(form.current_medications || ""),
      };
      if (payload.date_of_birth === "") payload.date_of_birth = null;
      await updatePatient.mutateAsync({ id, data: payload as any });
      toast({ title: "Patient updated" });
      setEditing(false);
    } catch (e: any) {
      toast({ title: e?.response?.data?.detail || "Update failed", variant: "destructive" });
    }
  };

  const handleUpload = async () => {
    if (!selectedFile) return;
    try {
      const fd = new FormData();
      fd.append("file", selectedFile);
      fd.append("title", uploadForm.title || selectedFile.name);
      fd.append("document_type", uploadForm.document_type);
      if (uploadForm.notes) fd.append("notes", uploadForm.notes);
      await uploadDocument.mutateAsync({ patientId: id, formData: fd });
      toast({ title: "Document uploaded" });
      setShowUpload(false);
      setSelectedFile(null);
      setUploadForm({ title: "", document_type: "other", notes: "" });
    } catch (e: any) {
      toast({ title: e?.response?.data?.detail || "Upload failed", variant: "destructive" });
    }
  };

  const handleDeleteDoc = async (docId: number) => {
    if (!confirm("Delete this document?")) return;
    try {
      await deleteDocument.mutateAsync({ patientId: id, docId });
      toast({ title: "Document deleted" });
    } catch (e: any) {
      toast({ title: e?.response?.data?.detail || "Delete failed", variant: "destructive" });
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
              {editing ? (
                <div>
                  <Label className="text-xs text-muted-foreground">Gender</Label>
                  <select
                    className="w-full border rounded-md px-3 py-2 text-sm"
                    value={form.gender}
                    onChange={(e) => setForm({ ...form, gender: e.target.value })}
                  >
                    <option value="">—</option>
                    <option value="male">Male</option>
                    <option value="female">Female</option>
                  </select>
                </div>
              ) : (
                <div className="text-sm"><span className="text-muted-foreground">Gender:</span> {patient.gender || "—"}</div>
              )}
              {editing ? (
                <div>
                  <Label className="text-xs text-muted-foreground">Date of Birth</Label>
                  <Input
                    type="date"
                    value={form.date_of_birth || ""}
                    onChange={(e) => setForm({ ...form, date_of_birth: e.target.value })}
                  />
                </div>
              ) : (
                <div className="text-sm"><span className="text-muted-foreground">DOB:</span> {patient.date_of_birth || "—"}</div>
              )}
              {editing ? (
                <div>
                  <Label className="text-xs text-muted-foreground">Blood Type</Label>
                  <select
                    className="w-full border rounded-md px-3 py-2 text-sm"
                    value={form.blood_type}
                    onChange={(e) => setForm({ ...form, blood_type: e.target.value })}
                  >
                    <option value="">—</option>
                    {["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"].map((bt) => (
                      <option key={bt} value={bt}>{bt}</option>
                    ))}
                  </select>
                </div>
              ) : (
                <div className="text-sm"><span className="text-muted-foreground">Blood Type:</span> {patient.blood_type || "—"}</div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Medical Info</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <ListField label="Allergies" value={form.allergies || ""} editing={editing} onChange={(v) => setForm({ ...form, allergies: v })} />
              <ListField label="Chronic Diseases" value={form.chronic_diseases || ""} editing={editing} onChange={(v) => setForm({ ...form, chronic_diseases: v })} />
              <ListField label="Current Medications" value={form.current_medications || ""} editing={editing} onChange={(v) => setForm({ ...form, current_medications: v })} />
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

        {/* Documents Section */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Documents</CardTitle>
            <Button size="sm" onClick={() => setShowUpload(true)}>
              <Upload className="mr-2 h-4 w-4" />Upload
            </Button>
          </CardHeader>
          <CardContent>
            {docsLoading ? (
              <LoadingSpinner />
            ) : !documents?.length ? (
              <p className="text-sm text-muted-foreground">No documents uploaded yet.</p>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {documents.map((doc) => (
                  <DocumentCard key={doc.id} doc={doc} patientId={id} onDelete={() => handleDeleteDoc(doc.id)} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Upload Dialog */}
      <Dialog open={showUpload} onOpenChange={setShowUpload}>
        <DialogContent>
          <DialogHeader><DialogTitle>Upload Document</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>File *</Label>
              <input
                ref={fileInputRef}
                type="file"
                className="w-full border rounded-md px-3 py-2 text-sm"
                onChange={(e) => {
                  const file = e.target.files?.[0] || null;
                  setSelectedFile(file);
                  if (file && !uploadForm.title) {
                    setUploadForm({ ...uploadForm, title: file.name });
                  }
                }}
              />
            </div>
            <div>
              <Label>Title</Label>
              <Input
                value={uploadForm.title}
                onChange={(e) => setUploadForm({ ...uploadForm, title: e.target.value })}
                placeholder="Document title"
              />
            </div>
            <div>
              <Label>Type</Label>
              <select
                className="w-full border rounded-md px-3 py-2 text-sm"
                value={uploadForm.document_type}
                onChange={(e) => setUploadForm({ ...uploadForm, document_type: e.target.value as DocumentType })}
              >
                {Object.entries(DOC_TYPE_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </div>
            <div>
              <Label>Notes</Label>
              <Input
                value={uploadForm.notes}
                onChange={(e) => setUploadForm({ ...uploadForm, notes: e.target.value })}
                placeholder="Optional notes"
              />
            </div>
            <Button
              className="w-full"
              onClick={handleUpload}
              disabled={!selectedFile || uploadDocument.isPending}
            >
              {uploadDocument.isPending ? "Uploading..." : "Upload Document"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </AppLayout>
  );
}

function DocumentCard({ doc, patientId, onDelete }: { doc: PatientDocument; patientId: number; onDelete: () => void }) {
  const isImage = isImageMime(doc.mime_type);
  const isPdf = doc.mime_type === "application/pdf";
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    let revoked = false;
    if (isImage || isPdf) {
      clinicDocumentsService.download(patientId, doc.id).then((res) => {
        if (!revoked) {
          const url = URL.createObjectURL(res.data);
          setBlobUrl(url);
        }
      }).catch(() => {});
    }
    return () => {
      revoked = true;
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patientId, doc.id, isImage, isPdf]);

  const handleClick = async () => {
    try {
      const res = await clinicDocumentsService.download(patientId, doc.id);
      const url = URL.createObjectURL(res.data);
      window.open(url, "_blank");
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch {
      // fallback: do nothing
    }
  };

  return (
    <div className="relative block border rounded-lg overflow-hidden hover:shadow-md transition-shadow group">
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(); }}
        className="absolute top-2 right-2 z-10 p-1 rounded-full bg-white/80 hover:bg-red-100 text-muted-foreground hover:text-red-600 opacity-0 group-hover:opacity-100 transition-opacity"
        title="Delete document"
      >
        <Trash2 className="h-4 w-4" />
      </button>
      <div
        onClick={handleClick}
        className="cursor-pointer"
      >
        <div className="h-40 bg-muted flex items-center justify-center">
          {isImage && blobUrl ? (
            <img
              src={blobUrl}
              alt={doc.title}
              className="h-full w-full object-cover"
            />
          ) : isPdf && blobUrl ? (
            <iframe
              src={blobUrl}
              title={doc.title}
              className="h-full w-full pointer-events-none"
              tabIndex={-1}
            />
          ) : (
            <FileText className="h-10 w-10 text-muted-foreground" />
          )}
        </div>
        <div className="p-3">
          <p className="font-medium text-sm truncate">{doc.title}</p>
          <div className="flex items-center justify-between mt-1">
            <Badge className={DOC_TYPE_COLORS[doc.document_type] || DOC_TYPE_COLORS.other}>
              {DOC_TYPE_LABELS[doc.document_type] || doc.document_type}
            </Badge>
            <span className="text-xs text-muted-foreground">{formatFileSize(doc.file_size)}</span>
          </div>
          {doc.notes && (
            <p className="text-xs text-muted-foreground mt-1 truncate">{doc.notes}</p>
          )}
          <p className="text-xs text-muted-foreground mt-1">
            {new Date(doc.uploaded_at).toLocaleDateString()}
          </p>
        </div>
      </div>
    </div>
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

function ListField({ label, value, editing, onChange }: { label: string; value: string; editing: boolean; onChange: (v: string) => void }) {
  if (editing) {
    return (
      <div>
        <Label className="text-xs text-muted-foreground">{label} (comma-separated)</Label>
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
    );
  }
  return (
    <div className="text-sm">
      <span className="text-muted-foreground">{label}:</span>{" "}
      {value || "None"}
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
