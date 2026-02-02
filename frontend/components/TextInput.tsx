'use client';

type TextInputProps = {
  value: string;
  onChange: (val: string) => void;
  placeholder?: string;
  readOnly?: boolean;
  disabled?: boolean; // ✅ Add this
  maxLength?: number;
};

export default function TextInput({
  value,
  onChange,
  placeholder = '',
  readOnly = false,
  disabled = false, // ✅ Default to false
  maxLength,
}: TextInputProps) {
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className="border border-gray-300 rounded px-3 py-1 w-[300px]"
      readOnly={readOnly}
      disabled={disabled} // ✅ Apply it here
      maxLength={maxLength}
    />
  );
}
