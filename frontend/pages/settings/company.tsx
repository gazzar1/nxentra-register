import { useEffect, useRef, useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useForm } from "react-hook-form";
import { Upload, Trash2, ImageIcon } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader, LoadingSpinner } from "@/components/common";
import {
  useCompanySettings,
  useUpdateCompanySettings,
  useUploadCompanyLogo,
  useDeleteCompanyLogo,
} from "@/queries/useCompanySettings";
import { useToast } from "@/components/ui/toaster";
import { getErrorMessage } from "@/lib/api-client";
import { currencyOptions } from "@/lib/constants";

interface CompanySettingsForm {
  name: string;
  name_ar: string;
  default_currency: string;
  fiscal_year_start_month: number;
}

export default function CompanySettingsPage() {
  const { t } = useTranslation(["common", "settings"]);
  const { toast } = useToast();
  const { data: settings, isLoading } = useCompanySettings();
  const updateSettings = useUpdateCompanySettings();
  const uploadLogo = useUploadCompanyLogo();
  const deleteLogo = useDeleteCompanyLogo();

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  const form = useForm<CompanySettingsForm>({
    defaultValues: {
      name: settings?.name || "",
      name_ar: settings?.name_ar || "",
      default_currency: settings?.default_currency || "USD",
      fiscal_year_start_month: settings?.fiscal_year_start_month || 1,
    },
  });

  // Update form when data loads
  useEffect(() => {
    if (settings) {
      form.reset({
        name: settings.name,
        name_ar: settings.name_ar || "",
        default_currency: settings.default_currency,
        fiscal_year_start_month: settings.fiscal_year_start_month,
      });
    }
  }, [settings]); // eslint-disable-line react-hooks/exhaustive-deps

  // Clear preview URL when settings logo_url is updated (query refetched)
  useEffect(() => {
    if (settings?.logo_url && previewUrl && !previewUrl.startsWith("data:")) {
      // Settings has a logo_url and we have a non-data preview, clear it
      setPreviewUrl(null);
    }
  }, [settings?.logo_url]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSubmit = async (data: CompanySettingsForm) => {
    try {
      await updateSettings.mutateAsync(data);
      toast({
        title: t("messages.success"),
        description: t("messages.saved"),
        variant: "success",
      });
    } catch (error) {
      toast({
        title: t("messages.error"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  const handleLogoUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    // Validate file type
    const allowedTypes = ["image/png", "image/jpeg", "image/gif", "image/webp"];
    if (!allowedTypes.includes(file.type)) {
      toast({
        title: t("messages.error"),
        description: t("settings:company.logoInvalidType", "Invalid file type. Please upload PNG, JPG, GIF, or WebP."),
        variant: "destructive",
      });
      return;
    }

    // Validate file size (5MB)
    if (file.size > 5 * 1024 * 1024) {
      toast({
        title: t("messages.error"),
        description: t("settings:company.logoTooLarge", "File too large. Maximum size is 5MB."),
        variant: "destructive",
      });
      return;
    }

    // Show preview
    const reader = new FileReader();
    reader.onload = (e) => {
      setPreviewUrl(e.target?.result as string);
    };
    reader.readAsDataURL(file);

    try {
      const result = await uploadLogo.mutateAsync(file);
      toast({
        title: t("messages.success"),
        description: t("settings:company.logoUploaded", "Logo uploaded successfully."),
        variant: "success",
      });
      // Use the returned logo_url with full backend URL (strip /api suffix)
      const logoUrl = result.data?.logo_url;
      if (logoUrl) {
        const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
        const baseUrl = apiUrl.replace(/\/api\/?$/, "");
        setPreviewUrl(`${baseUrl}${logoUrl}`);
      }
    } catch (error) {
      toast({
        title: t("messages.error"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
      setPreviewUrl(null);
    }

    // Reset file input
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleLogoDelete = async () => {
    try {
      await deleteLogo.mutateAsync();
      toast({
        title: t("messages.success"),
        description: t("settings:company.logoDeleted", "Logo deleted successfully."),
        variant: "success",
      });
    } catch (error) {
      toast({
        title: t("messages.error"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  const months = Array.from({ length: 12 }, (_, i) => ({
    value: i + 1,
    label: t(`settings:months.${i + 1}`),
  }));

  // Get the logo URL - either preview or from settings
  const displayLogoUrl = previewUrl || settings?.logo_url;
  // Construct full URL for the logo - use base URL without /api for media files
  const fullLogoUrl = (() => {
    if (!displayLogoUrl) return null;
    // Data URLs don't need modification
    if (displayLogoUrl.startsWith("data:")) return displayLogoUrl;
    // If it's already a full URL, use it directly
    if (displayLogoUrl.startsWith("http")) return displayLogoUrl;
    // Get base URL (strip /api suffix if present)
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    const baseUrl = apiUrl.replace(/\/api\/?$/, "");
    return `${baseUrl}${displayLogoUrl}`;
  })();

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("settings:company.title")}
          subtitle={t("settings:company.subtitle")}
        />

        {isLoading ? (
          <div className="flex justify-center py-12">
            <LoadingSpinner size="lg" />
          </div>
        ) : (
          <div className="space-y-6 max-w-2xl">
            {/* Company Logo Card */}
            <Card>
              <CardHeader>
                <CardTitle>{t("settings:company.logo", "Company Logo")}</CardTitle>
                <CardDescription>
                  {t("settings:company.logoDescription", "Upload your company logo. This will be used on invoices and reports.")}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="flex items-start gap-6">
                  {/* Logo Preview */}
                  <div className="flex-shrink-0">
                    <div className="w-32 h-32 border-2 border-dashed border-muted-foreground/25 rounded-lg flex items-center justify-center bg-muted/50 overflow-hidden">
                      {fullLogoUrl ? (
                        <img
                          src={fullLogoUrl}
                          alt={t("settings:company.logoAlt", "Company logo")}
                          className="w-full h-full object-contain"
                          onError={(e) => {
                            console.error("Failed to load logo:", fullLogoUrl);
                            // Hide broken image icon
                            e.currentTarget.style.display = "none";
                          }}
                        />
                      ) : (
                        <ImageIcon className="w-12 h-12 text-muted-foreground/50" />
                      )}
                    </div>
                  </div>

                  {/* Upload Controls */}
                  <div className="flex-1 space-y-4">
                    <div className="space-y-2">
                      <p className="text-sm text-muted-foreground">
                        {t("settings:company.logoHint", "PNG, JPG, GIF or WebP. Max 5MB.")}
                      </p>
                    </div>

                    <div className="flex gap-2">
                      <input
                        ref={fileInputRef}
                        type="file"
                        accept="image/png,image/jpeg,image/gif,image/webp"
                        onChange={handleLogoUpload}
                        className="hidden"
                        id="logo-upload"
                      />
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => fileInputRef.current?.click()}
                        disabled={uploadLogo.isPending}
                      >
                        <Upload className="w-4 h-4 me-2" />
                        {uploadLogo.isPending
                          ? t("actions.loading")
                          : t("settings:company.uploadLogo", "Upload Logo")}
                      </Button>

                      {settings?.logo_url && (
                        <Button
                          type="button"
                          variant="destructive"
                          onClick={handleLogoDelete}
                          disabled={deleteLogo.isPending}
                        >
                          <Trash2 className="w-4 h-4 me-2" />
                          {deleteLogo.isPending
                            ? t("actions.loading")
                            : t("settings:company.deleteLogo", "Delete")}
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* Company Settings Card */}
            <Card>
              <CardHeader>
                <CardTitle>{t("settings:company.generalSettings", "General Settings")}</CardTitle>
              </CardHeader>
              <CardContent>
                <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-6">
                  {/* Company Name */}
                  <div className="space-y-2">
                    <Label htmlFor="name">{t("settings:company.name")}</Label>
                    <Input
                      id="name"
                      {...form.register("name")}
                    />
                  </div>

                  {/* Company Name (Arabic) */}
                  <div className="space-y-2">
                    <Label htmlFor="name_ar">{t("settings:company.nameAr")}</Label>
                    <Input
                      id="name_ar"
                      {...form.register("name_ar")}
                      dir="rtl"
                    />
                  </div>

                  {/* Default Currency */}
                  <div className="space-y-2">
                    <Label htmlFor="default_currency">{t("settings:company.currency")}</Label>
                    <Select
                      value={form.watch("default_currency")}
                      onValueChange={(value) => form.setValue("default_currency", value)}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {currencyOptions.map((currency) => (
                          <SelectItem key={currency} value={currency}>
                            {currency} - {t(`currency.${currency}`, currency)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  {/* Fiscal Year Start */}
                  <div className="space-y-2">
                    <Label htmlFor="fiscal_year_start_month">
                      {t("settings:company.fiscalYearStart")}
                    </Label>
                    <Select
                      value={form.watch("fiscal_year_start_month")?.toString()}
                      onValueChange={(value) =>
                        form.setValue("fiscal_year_start_month", parseInt(value))
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {months.map((month) => (
                          <SelectItem key={month.value} value={month.value.toString()}>
                            {month.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <Button type="submit" disabled={updateSettings.isPending}>
                    {updateSettings.isPending ? t("actions.loading") : t("actions.save")}
                  </Button>
                </form>
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common", "settings"])),
    },
  };
};
