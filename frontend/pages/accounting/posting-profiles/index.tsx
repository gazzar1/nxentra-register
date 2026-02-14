import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { Plus, Search, CreditCard, Pencil, Trash2, Star } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import { usePostingProfiles, useDeletePostingProfile } from "@/queries/useSales";
import { useToast } from "@/components/ui/toaster";
import type { PostingProfile, PostingProfileType } from "@/types/sales";
import { cn } from "@/lib/cn";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const PROFILE_TYPE_LABELS: Record<PostingProfileType, string> = {
  CUSTOMER: "Customer (AR)",
  VENDOR: "Vendor (AP)",
};

const PROFILE_TYPE_COLORS: Record<PostingProfileType, string> = {
  CUSTOMER: "bg-green-100 text-green-800",
  VENDOR: "bg-blue-100 text-blue-800",
};

export default function PostingProfilesPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { data: profiles, isLoading } = usePostingProfiles();
  const deleteProfile = useDeletePostingProfile();
  const [search, setSearch] = useState("");
  const [deleteDialog, setDeleteDialog] = useState<{ open: boolean; profile: PostingProfile | null }>({
    open: false,
    profile: null,
  });

  const filteredProfiles = profiles?.filter((p) => {
    if (!search) return true;
    const searchLower = search.toLowerCase();
    return (
      p.code.toLowerCase().includes(searchLower) ||
      p.name.toLowerCase().includes(searchLower) ||
      p.name_ar?.toLowerCase().includes(searchLower)
    );
  });

  const handleDelete = async () => {
    if (!deleteDialog.profile) return;

    try {
      await deleteProfile.mutateAsync(deleteDialog.profile.id);
      toast({
        title: "Posting profile deleted",
        description: `${deleteDialog.profile.name} has been deleted.`,
      });
    } catch (error) {
      toast({
        title: "Error",
        description: "Failed to delete posting profile.",
        variant: "destructive",
      });
    } finally {
      setDeleteDialog({ open: false, profile: null });
    }
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Posting Profiles"
          subtitle="Configure control accounts for customers and vendors"
          actions={
            <Link href="/accounting/posting-profiles/new">
              <Button>
                <Plus className="h-4 w-4 me-2" />
                Add Profile
              </Button>
            </Link>
          }
        />

        <Card>
          <CardContent className="p-6">
            {/* Search */}
            <div className="flex items-center gap-4 mb-6">
              <div className="relative flex-1 max-w-md">
                <Search className="absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Search posting profiles..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="ps-10"
                />
              </div>
            </div>

            {/* Content */}
            {isLoading ? (
              <LoadingSpinner />
            ) : !filteredProfiles?.length ? (
              <EmptyState
                icon={<CreditCard className="h-12 w-12" />}
                title="No posting profiles yet"
                description="Add posting profiles to configure control accounts for AR and AP."
                action={
                  <Link href="/accounting/posting-profiles/new">
                    <Button>
                      <Plus className="h-4 w-4 me-2" />
                      Add Profile
                    </Button>
                  </Link>
                }
              />
            ) : (
              <div className="space-y-2">
                {/* Header */}
                <div className="grid grid-cols-12 gap-4 px-4 py-2 text-sm font-medium text-muted-foreground border-b">
                  <div className="col-span-2">Code</div>
                  <div className="col-span-3">Name</div>
                  <div className="col-span-2">Type</div>
                  <div className="col-span-3">Control Account</div>
                  <div className="col-span-1">Status</div>
                  <div className="col-span-1"></div>
                </div>

                {/* Rows */}
                {filteredProfiles.map((profile) => (
                  <div
                    key={profile.id}
                    className="grid grid-cols-12 gap-4 px-4 py-3 rounded-lg border hover:bg-muted/50 transition-colors items-center"
                  >
                    <div className="col-span-2">
                      <span className="font-mono text-sm ltr-code">{profile.code}</span>
                    </div>
                    <div className="col-span-3">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{profile.name}</span>
                        {profile.is_default && (
                          <Star className="h-4 w-4 text-yellow-500 fill-yellow-500" />
                        )}
                      </div>
                      {profile.name_ar && (
                        <p className="text-sm text-muted-foreground" dir="rtl">
                          {profile.name_ar}
                        </p>
                      )}
                    </div>
                    <div className="col-span-2">
                      <Badge className={cn("text-xs", PROFILE_TYPE_COLORS[profile.profile_type])}>
                        {PROFILE_TYPE_LABELS[profile.profile_type]}
                      </Badge>
                    </div>
                    <div className="col-span-3 text-sm">
                      {profile.control_account_code ? (
                        <div>
                          <span className="font-mono ltr-code">{profile.control_account_code}</span>
                          {profile.control_account_name && (
                            <p className="text-muted-foreground truncate">{profile.control_account_name}</p>
                          )}
                        </div>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </div>
                    <div className="col-span-1">
                      {profile.is_active ? (
                        <Badge variant="default" className="bg-green-500">Active</Badge>
                      ) : (
                        <Badge variant="secondary">Inactive</Badge>
                      )}
                    </div>
                    <div className="col-span-1 flex items-center justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => router.push(`/accounting/posting-profiles/${profile.id}/edit`)}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setDeleteDialog({ open: true, profile })}
                      >
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Delete confirmation dialog */}
      <AlertDialog open={deleteDialog.open} onOpenChange={(open: boolean) => setDeleteDialog({ open, profile: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Posting Profile</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete &quot;{deleteDialog.profile?.name}&quot;? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} className="bg-destructive text-destructive-foreground">
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common", "accounting"])),
    },
  };
};
