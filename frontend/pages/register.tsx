import Link from "next/link";
import { useRouter } from "next/router";
import { FormEvent, useState } from "react";
import { AuthLayout } from "@/components/AuthLayout";
import { InputField, SelectField } from "@/components/FormField";
import {
  accountingPeriods,
  currencyOptions,
  dateFormats,
  decimalPlaces,
  decimalSeparators,
  languageOptions,
  thousandSeparators
} from "@/lib/constants";
import { register } from "@/lib/api";

const initialState = {
  email: "",
  name: "",
  password: "",
  company_name: "",
  currency: "USD",
  language: "en",
  periods: "12",
  current_period: "1",
  thousand_separator: ",",
  decimal_places: "2",
  decimal_separator: ".",
  date_format: "dd/mm/yyyy"
};

type Errors = Partial<Record<keyof typeof initialState, string>>;

export default function RegisterPage() {
  const router = useRouter();
  const [form, setForm] = useState(initialState);
  const [errors, setErrors] = useState<Errors>({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleChange = (field: keyof typeof form) => (value: string) => {
    setForm((previous) => {
      const nextForm = { ...previous, [field]: value };
      if (field === "periods") {
        const total = Number(value);
        if (Number(nextForm.current_period) > total) {
          nextForm.current_period = String(total);
        }
      }
      return nextForm;
    });
  };

  const validate = () => {
    const validationErrors: Errors = {};

    if (!form.email) validationErrors.email = "Email is required";
    if (!form.name) validationErrors.name = "Name is required";
    if (!form.password || form.password.length < 8)
      validationErrors.password = "Password must be at least 8 characters";
    if (!form.company_name)
      validationErrors.company_name = "Company database name is required";
    if (form.company_name && form.company_name.includes(" "))
      validationErrors.company_name = "Use a single word with no spaces";
    if (form.company_name.length > 10)
      validationErrors.company_name = "Maximum 10 characters";

    const currentPeriodNumber = Number(form.current_period);
    const totalPeriodsNumber = Number(form.periods);
    if (currentPeriodNumber > totalPeriodsNumber) {
      validationErrors.current_period = "Cannot exceed total periods";
    }

    setErrors(validationErrors);
    return Object.keys(validationErrors).length === 0;
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!validate()) return;

    try {
      setIsSubmitting(true);
      await register({
        email: form.email,
        name: form.name,
        password: form.password,
        company_name: form.company_name,
        currency: form.currency,
        language: form.language,
        periods: Number(form.periods),
        current_period: Number(form.current_period),
        thousand_separator: form.thousand_separator === "none" ? "" : form.thousand_separator,
        decimal_places: Number(form.decimal_places),
        decimal_separator: form.decimal_separator,
        date_format: form.date_format
      });

      // Redirect to verify-email page
      router.push(`/verify-email?email=${encodeURIComponent(form.email)}&sent=true`);
    } catch (error: unknown) {
      console.error(error);
      const axiosError = error as { response?: { data?: { detail?: string; email?: string[] } } };
      const detail = axiosError.response?.data?.detail;
      const emailError = axiosError.response?.data?.email?.[0];
      setErrors({ email: emailError || detail || "Registration failed. Please try again." });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <AuthLayout>
      <div className="relative">
        <form onSubmit={handleSubmit} className="grid grid-cols-1 gap-6 md:grid-cols-2">
          <div className="md:col-span-2">
            <h2 className="text-2xl font-semibold text-foreground">Create your company workspace</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              Enter basic account credentials and configure the accounting experience for your ERP tenant.
            </p>
          </div>
          <InputField id="email" label="Email" type="email" value={form.email} onChange={handleChange("email")} error={errors.email} />
          <InputField id="name" label="Full name" value={form.name} onChange={handleChange("name")} error={errors.name} />
          <InputField
            id="password"
            label="Password"
            type="password"
            value={form.password}
            onChange={handleChange("password")}
            error={errors.password}
          />
          <InputField
            id="company_name"
            label="Company database name"
            value={form.company_name}
            onChange={handleChange("company_name")}
            placeholder="e.g. nxentra"
            error={errors.company_name}
          />

          <SelectField id="currency" label="Functional currency" value={form.currency} onChange={handleChange("currency")}>
            {currencyOptions.map((currency) => (
              <option key={currency} value={currency}>
                {currency}
              </option>
            ))}
          </SelectField>

          <SelectField id="language" label="Interface language" value={form.language} onChange={handleChange("language")}>
            {languageOptions.map((language) => (
              <option key={language.value} value={language.value}>
                {language.label}
              </option>
            ))}
          </SelectField>

          <SelectField
            id="periods"
            label="Number of accounting periods"
            value={form.periods}
            onChange={handleChange("periods")}
          >
            {accountingPeriods.map((period) => (
              <option key={period.value} value={period.value}>
                {period.label}
              </option>
            ))}
          </SelectField>

          <SelectField
            id="current_period"
            label="Current accounting period"
            value={form.current_period}
            onChange={handleChange("current_period")}
            error={errors.current_period}
          >
            {Array.from({ length: Math.max(1, Number(form.periods) || 1) }, (_, index) => index + 1).map((period) => (
              <option key={period} value={period}>
                {period}
              </option>
            ))}
          </SelectField>

          <SelectField
            id="thousand_separator"
            label="Thousands separator"
            value={form.thousand_separator}
            onChange={handleChange("thousand_separator")}
          >
            {thousandSeparators.map((separator) => (
              <option key={separator} value={separator}>
                {separator === "none" ? "None" : separator}
              </option>
            ))}
          </SelectField>

          <SelectField
            id="decimal_places"
            label="Number of decimal places"
            value={form.decimal_places}
            onChange={handleChange("decimal_places")}
          >
            {decimalPlaces.map((places) => (
              <option key={places} value={places}>
                {places}
              </option>
            ))}
          </SelectField>

          <SelectField
            id="decimal_separator"
            label="Decimal separator"
            value={form.decimal_separator}
            onChange={handleChange("decimal_separator")}
          >
            {decimalSeparators.map((separator) => (
              <option key={separator} value={separator}>
                {separator}
              </option>
            ))}
          </SelectField>

          <SelectField
            id="date_format"
            label="Preferred date format"
            value={form.date_format}
            onChange={handleChange("date_format")}
          >
            {dateFormats.map((format) => (
              <option key={format} value={format}>
                {format}
              </option>
            ))}
          </SelectField>

          <div className="md:col-span-2 flex flex-col gap-3">
            <button
              type="submit"
              className="rounded-full bg-accent px-8 py-3 text-center font-semibold text-accent-foreground shadow-lg shadow-accent/30 transition hover:bg-accent/90"
              disabled={isSubmitting}
            >
              {isSubmitting ? "Submitting..." : "Launch Workspace"}
            </button>
            <p className="text-sm text-muted-foreground">
              Already onboarded? <Link href="/login">Login</Link>
            </p>
          </div>
        </form>
      </div>
    </AuthLayout>
  );
}
