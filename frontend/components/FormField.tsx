import { clsx } from "clsx";
import { Eye, EyeOff } from "lucide-react";
import { HTMLInputTypeAttribute, ReactNode, useState } from "react";

interface BaseProps {
  id: string;
  label: string;
  children?: ReactNode;
  error?: string;
  className?: string;
}

interface InputProps extends BaseProps {
  type?: HTMLInputTypeAttribute;
  value: string | number;
  onChange: (value: string) => void;
  placeholder?: string;
}

export function InputField({
  id,
  label,
  type = "text",
  value,
  onChange,
  placeholder,
  error,
  className
}: InputProps) {
  return (
    <div className={clsx("space-y-2", className)}>
      <label className="block text-sm font-medium text-foreground" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        name={id}
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className={clsx(
          "w-full rounded-xl border border-input bg-background px-4 py-3 text-foreground transition",
          "placeholder:text-muted-foreground focus:border-accent focus:outline-none focus:ring focus:ring-accent/20",
          error && "border-destructive focus:border-destructive focus:ring-destructive/20"
        )}
      />
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}

interface PasswordProps extends BaseProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  hint?: ReactNode;
}

export function PasswordField({
  id,
  label,
  value,
  onChange,
  placeholder,
  error,
  hint,
  className
}: PasswordProps) {
  const [visible, setVisible] = useState(false);
  return (
    <div className={clsx("space-y-2", className)}>
      <label className="block text-sm font-medium text-foreground" htmlFor={id}>
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          name={id}
          type={visible ? "text" : "password"}
          value={value}
          placeholder={placeholder}
          onChange={(event) => onChange(event.target.value)}
          className={clsx(
            "w-full rounded-xl border border-input bg-background px-4 py-3 pr-12 text-foreground transition",
            "placeholder:text-muted-foreground focus:border-accent focus:outline-none focus:ring focus:ring-accent/20",
            error && "border-destructive focus:border-destructive focus:ring-destructive/20"
          )}
        />
        <button
          type="button"
          onClick={() => setVisible((previous) => !previous)}
          aria-label={visible ? "Hide password" : "Show password"}
          className="absolute inset-y-0 right-0 flex items-center px-3 text-muted-foreground transition hover:text-foreground"
        >
          {visible ? <EyeOff size={20} aria-hidden="true" /> : <Eye size={20} aria-hidden="true" />}
        </button>
      </div>
      {error ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : hint ? (
        <p className="text-sm text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}

interface SelectProps extends BaseProps {
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
}

export function SelectField({ id, label, value, onChange, children, error }: SelectProps) {
  return (
    <div className="space-y-2">
      <label className="block text-sm font-medium text-foreground" htmlFor={id}>
        {label}
      </label>
      <select
        id={id}
        name={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={clsx(
          "w-full rounded-xl border border-input bg-background px-4 py-3 text-foreground transition",
          "focus:border-accent focus:outline-none focus:ring focus:ring-accent/20",
          error && "border-destructive focus:border-destructive focus:ring-destructive/20"
        )}
      >
        {children}
      </select>
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}
