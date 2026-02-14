import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect } from "react";
import { ArrowLeft, Save } from "lucide-react";
import { useForm, Controller } from "react-hook-form";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { PageHeader, LoadingSpinner } from "@/components/common";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAccounts } from "@/queries/useAccounts";
import { usePostingProfile, useUpdatePostingProfile } from "@/queries/useSales";
import { useToast } from "@/components/ui/toaster";
import type { PostingProfileType } from "@/types/sales";

interface PostingProfileFormData {
  code: string;
  name: string;
  name_ar: string;
  profile_type: PostingProfileType;
  control_account_id: string;
  is_default: boolean;
}

const PROFILE_TYPES: { value: PostingProfileType; label: string }[] = [
  { value: "CUSTOMER", label: "Customer (AR)" },
  { value: "VENDOR", label: "Vendor (AP)" },
];

export default function EditPostingProfilePage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { id } = router.query;
  const { toast } = useToast();
  const { data: profile, isLoading } = usePostingProfile(parseInt(id as string));
  const { data: accounts } = useAccounts();
  const updateProfile = useUpdatePostingProfile();

  // Control accounts are typically AR (Asset) or AP (Liability)
  const controlAccounts = accounts?.filter(
    (a) => (a.account_type === "ASSET" || a.account_type === "LIABILITY") && a.is_postable && !a.is_header
  );

  const {
    register,
    control,
    handleSubmit,
    watch,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<PostingProfileFormData>({
    defaultValues: {
      code: "",
      name: "",
      name_ar: "",
      profile_type: "CUSTOMER",
      control_account_id: "",
      is_default: false,
    },
  });

  const profileType = watch("profile_type");

  // Filter accounts based on profile type
  const filteredAccounts = controlAccounts?.filter((acc) => {
    if (profileType === "CUSTOMER") {
      return acc.account_type === "ASSET"; // AR accounts
    } else {
      return acc.account_type === "LIABILITY"; // AP accounts
    }
  });

  // Populate form when profile data loads
  useEffect(() => {
    if (profile) {
      reset({
        code: profile.code,
        name: profile.name,
        name_ar: profile.name_ar || "",
        profile_type: profile.profile_type,
        control_account_id: profile.control_account?.toString() || "",
        is_default: profile.is_default,
      });
    }
  }, [profile, reset]);

  const onSubmit = async (data: PostingProfileFormData) => {
    if (!profile) return;

    try {
      await updateProfile.mutateAsync({
        id: profile.id,
        data: {
          code: data.code,
          name: data.name,
          name_ar: data.name_ar || undefined,
          profile_type: data.profile_type,
          control_account_id: parseInt(data.control_account_id),
          is_default: data.is_default,
        },
      });
      toast({
        title: "Posting profile updated",
        description: `${data.name} has been updated successfully.`,
      });
      router.push("/accounting/posting-profiles");
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.error || "Failed to update posting profile.",
        variant: "destructive",
      });
    }
  };

  if (isLoading) {
    return (
      <AppLayout>
        <LoadingSpinner />
      </AppLayout>
    );
  }

  if (!profile) {
    return (
      <AppLayout>
        <div className="text-center py-12">
          <p className="text-muted-foreground">Posting profile not found</p>
          <Link href="/accounting/posting-profiles">
            <Button variant="link">Back to posting profiles</Button>
          </Link>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
        <PageHeader
          title="Edit Posting Profile"
          subtitle={`Editing ${profile.name}`}
          actions={
            <div className="flex gap-2">
              <Link href="/accounting/posting-profiles">
                <Button type="button" variant="outline">
                  <ArrowLeft className="h-4 w-4 me-2" />
                  Cancel
                </Button>
              </Link>
              <Button type="submit" disabled={isSubmitting}>
                <Save className="h-4 w-4 me-2" />
                {isSubmitting ? "Saving..." : "Save Changes"}
              </Button>
            </div>
          }
        />

        <Card>
          <CardHeader>
            <CardTitle>Profile Details</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="code">Profile Code *</Label>
              <Input
                id="code"
                {...register("code", { required: "Profile code is required" })}
                placeholder="AR-DEFAULT"
              />
              {errors.code && (
                <p className="text-sm text-destructive">{errors.code.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="profile_type">Profile Type *</Label>
              <Controller
                name="profile_type"
                control={control}
                render={({ field }) => (
                  <Select onValueChange={field.onChange} value={field.value}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select type" />
                    </SelectTrigger>
                    <SelectContent>
                      {PROFILE_TYPES.map((type) => (
                        <SelectItem key={type.value} value={type.value}>
                          {type.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="name">Name (English) *</Label>
              <Input
                id="name"
                {...register("name", { required: "Name is required" })}
                placeholder="Default AR Profile"
              />
              {errors.name && (
                <p className="text-sm text-destructive">{errors.name.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="name_ar">Name (Arabic)</Label>
              <Input
                id="name_ar"
                {...register("name_ar")}
                placeholder="ملف الترحيل الافتراضي"
                dir="rtl"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="control_account_id">Control Account *</Label>
              <Controller
                name="control_account_id"
                control={control}
                rules={{ required: "Control account is required" }}
                render={({ field }) => (
                  <Select onValueChange={field.onChange} value={field.value}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select account" />
                    </SelectTrigger>
                    <SelectContent>
                      {filteredAccounts?.map((acc) => (
                        <SelectItem key={acc.id} value={acc.id.toString()}>
                          {acc.code} - {acc.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              <p className="text-xs text-muted-foreground">
                {profileType === "CUSTOMER"
                  ? "Select an Accounts Receivable (AR) account"
                  : "Select an Accounts Payable (AP) account"}
              </p>
              {errors.control_account_id && (
                <p className="text-sm text-destructive">{errors.control_account_id.message}</p>
              )}
            </div>

            <div className="space-y-2 flex items-center gap-2 pt-6">
              <Controller
                name="is_default"
                control={control}
                render={({ field }) => (
                  <Checkbox
                    id="is_default"
                    checked={field.value}
                    onCheckedChange={field.onChange}
                  />
                )}
              />
              <Label htmlFor="is_default" className="cursor-pointer">
                Set as default profile for this type
              </Label>
            </div>
          </CardContent>
        </Card>
      </form>
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
