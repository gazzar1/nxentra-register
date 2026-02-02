import { clsx } from "clsx";
import { HTMLInputTypeAttribute, ReactNode } from "react";

interface BaseProps {
  id: string;
  label: string;
  children?: ReactNode;
  error?: string;
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
  error
}: InputProps) {
  return (
    <div className="space-y-2">
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
