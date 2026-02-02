import { useEffect, useState } from 'react';
import CreatableSelect from 'react-select/creatable';
import axiosClient from '../lib/api';

type OptionType = {
  value: string;
  label: string;
};

type Account = {
  code: string;
  name: string;
};

type Props = {
  value: string;
  onChange: (code: string, name: string) => void;
  isDisabled?: boolean;
};

export default function CreatableSelectInternal({
  value,
  onChange,
  isDisabled = false,
}: Props) {
  const [options, setOptions] = useState<OptionType[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [codeToNameMap, setCodeToNameMap] = useState<Record<string, string>>({});

  const fetchOptions = async () => {
    try {
      const response = await axiosClient.get('/api/accounts/');
      const data: Account[] = response.data;

      const codeNameMap: Record<string, string> = {};
      const formatted = data.map((item) => {
        codeNameMap[item.code] = item.name;
        return {
          value: item.code,
          label: `${item.code} - ${item.name}`,
        };
      });

      setOptions(formatted);
      setCodeToNameMap(codeNameMap);
    } catch (error) {
      console.error('Failed to fetch accounts:', error);
    }
  };

  useEffect(() => {
    fetchOptions(); // Initial fetch

    let socket: WebSocket | null = null;

    try {
     const socket = new WebSocket('ws://127.0.0.1:8000/ws/accounts/');


      socket.onopen = () => {
        console.log('Dropdown WebSocket connected.');
      };

      socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.message === 'Account list updated') {
          console.log('Refreshing dropdown options...');
          fetchOptions();
        }
      };

      socket.onerror = (event) => {
        console.error('Dropdown WebSocket error: a connection issue occurred.', event);
      };
    } catch (err) {
      console.error('WebSocket setup failed:', err);
    }

    return () => {
      if (socket) {
        socket.close();
      }
    };
  }, []);

  const handleSelect = (selected: OptionType | null) => {
    if (!selected) {
      onChange('', '');
      return;
    }

    const code = selected.value;
    const name = codeToNameMap[code] || '';
    onChange(code, name);
  };

  const handleCreate = (input: string) => {
    const trimmed = input.slice(0, 10);
    setOptions((prev) => [...prev, { value: trimmed, label: trimmed }]);
    onChange(trimmed, '');
  };

  return (
    <div className="w-[160px]">
      <CreatableSelect
        isClearable
        value={{ value, label: value }}
        onChange={handleSelect}
        onCreateOption={handleCreate}
        options={options}
        placeholder=""
        isDisabled={isDisabled}
        inputValue={inputValue}
        onInputChange={(val) => {
          if (val.length <= 10) setInputValue(val);
        }}
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
