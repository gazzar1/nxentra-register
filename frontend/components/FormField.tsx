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
      <label className="block text-sm font-medium text-slate-200" htmlFor={id}>
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
          "w-full rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-3 text-slate-100 transition",
          "placeholder:text-slate-500 focus:border-accent focus:outline-none focus:ring focus:ring-accent/20",
          error && "border-red-500/70 focus:border-red-500/70 focus:ring-red-500/20"
        )}
      />
      {error && <p className="text-sm text-red-400">{error}</p>}
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
      <label className="block text-sm font-medium text-slate-200" htmlFor={id}>
        {label}
      </label>
      <select
        id={id}
        name={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={clsx(
          "w-full rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-3 text-slate-100 transition",
          "focus:border-accent focus:outline-none focus:ring focus:ring-accent/20",
          error && "border-red-500/70 focus:border-red-500/70 focus:ring-red-500/20"
        )}
      >
        {children}
      </select>
      {error && <p className="text-sm text-red-400">{error}</p>}
    </div>
  );
}
