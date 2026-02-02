'use client';

import CreatableSelect from 'react-select/creatable';

type OptionType = {
  value: string;  // account code
  label: string;  // currently "code - name"
  name: string;   // account name
};

type Props = {
  value: string;
  onChange: (code: string, name: string) => void;
  options: OptionType[];
  isDisabled?: boolean;
};

export default function SelectWithCreate({
  value,
  onChange,
  options,
  isDisabled = false,
}: Props) {
  const handleSelect = (selected: any) => {
    if (!selected) {
      onChange('', '');
      return;
    }

    const code = selected.value as string;
    const found = options.find((o) => o.value === code);
    const name = found?.name ?? '';
    onChange(code, name);
  };

  const handleCreate = (input: string) => {
    const trimmed = input.slice(0, 10);
    onChange(trimmed, '');
  };

  const selectedOption =
    value
      ? options.find((o) => o.value === value) ?? { value, label: value, name: '' }
      : null;

  return (
    <div className="w-[160px]">
      <CreatableSelect
        isClearable
        value={selectedOption}
        onChange={handleSelect}
        onCreateOption={handleCreate}
        options={options}
        placeholder=""
        isDisabled={isDisabled}
        formatOptionLabel={(option: OptionType, { context }) =>
          context === 'menu'
            ? `${option.value} - ${option.name || option.label}` // dropdown
            : option.value                                       // selected value
        }
        styles={{
          control: (base) => ({
            ...base,
            minHeight: '32px',
            height: '32px',
            fontSize: '14px',
          }),
          menu: (base) => ({
            ...base,
            width: '300px',
            zIndex: 9999,
          }),
        }}
      />
    </div>
  );
}
