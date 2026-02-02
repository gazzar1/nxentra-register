'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import axiosClient from '../../lib/api';
import { getAccessToken, removeAccessToken } from '../../lib/auth-storage';
import SelectWithCreate from '../../components/CreatableSelect';
import TextInput from '../../components/TextInput';
import { Button } from '../../components/ui/button';
import { accountsService, dimensionsService } from '@/services/accounts.service';
import type { AnalysisDimension, AccountAnalysisDefault } from '@/types/account';




type Account = {
  code: string;
  name: string;
  type: string;
  status: string;
};

const getAuthHeaders = () => {
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
};

export default function HomePage() {
  const router = useRouter();

  const [userName, setUserName] = useState<string>('User');

  const [accountCode, setAccountCode] = useState('');
  const [accountName, setAccountName] = useState('');
  const [accountType, setAccountType] = useState('');
  const [accountStatus, setAccountStatus] = useState('');
  const [accountList, setAccountList] = useState<Account[]>([]);
  const [isEditing, setIsEditing] = useState(false);
  const [currentIndex, setCurrentIndex] = useState<number | null>(null);

  // Analysis dimensions state
  const [dimensions, setDimensions] = useState<AnalysisDimension[]>([]);
  const [accountDefaults, setAccountDefaults] = useState<AccountAnalysisDefault[]>([]);
  // For CoA-type dimensions: selected value id per dimension
  const [dimSelections, setDimSelections] = useState<Record<number, string>>({});
  // For Journal-type dimensions: checked state per dimension
  const [dimChecks, setDimChecks] = useState<Record<number, boolean>>({});


  // -------- API calls --------

  const fetchUserProfile = async () => {
    try {
      const response = await axiosClient.get('/profile/', {
        headers: getAuthHeaders(),
      });

      console.log('PROFILE DATA', response.data);

      const d = (response.data as any).user ?? response.data;

      const name =
        (d.first_name && d.first_name.trim()) ||
        (d.username && d.username.trim()) ||
        d.email ||
        'User';

      setUserName(name);
    } catch (error: unknown) {
      console.error('Failed to fetch user profile', error);

      const axiosError = error as { response?: { status?: number } };
      if (axiosError?.response?.status === 401) {
        // token invalid or expired → force logout
        handleLogout();
        return;
      }

      setUserName('User');
    }
  };

  const fetchAccounts = async () => {
    try {
      const response = await axiosClient.get('/accounts/', {
        headers: getAuthHeaders(),
      });
      console.log('ACCOUNTS DATA', response.data);
      setAccountList(response.data);
      setCurrentIndex(-1);
    } catch (error: unknown) {
      console.error('Failed to fetch accounts', error);
      const axiosError = error as { response?: { status?: number } };
      if (axiosError?.response?.status === 401) {
        handleLogout();
      }
    }
  };

  const fetchDimensions = async () => {
    try {
      const { data } = await dimensionsService.list();
      setDimensions(data);
    } catch (error) {
      console.error('Failed to fetch dimensions', error);
    }
  };

  const fetchAccountDefaults = async (code: string) => {
    if (!code) {
      setAccountDefaults([]);
      setDimSelections({});
      setDimChecks({});
      return;
    }
    try {
      const { data } = await accountsService.getAnalysisDefaults(code);
      setAccountDefaults(data);
      // Build selections/checks from defaults
      const selections: Record<number, string> = {};
      const checks: Record<number, boolean> = {};
      data.forEach((def) => {
        const dim = dimensions.find(d => d.id === def.dimension);
        if (dim) {
          if (dim.applies_to_account_types.length > 0) {
            // CoA type: store selected value
            selections[dim.id] = def.default_value.toString();
          } else {
            // Journal type: mark as checked
            checks[dim.id] = true;
          }
        }
      });
      setDimSelections(selections);
      setDimChecks(checks);
    } catch (error) {
      console.error('Failed to fetch account defaults', error);
      setAccountDefaults([]);
      setDimSelections({});
      setDimChecks({});
    }
  };



  useEffect(() => {
    const token = getAccessToken();

    console.log('TOKEN FROM STORAGE:', token);  // debug

    if (!token) {
      // No token? User is not logged in → kick them out
      router.replace('/login');
      return;
    }

    fetchUserProfile();
    fetchAccounts();
    fetchDimensions();
  }, [router]);


  // -------- handlers --------

    const handleLogout = () => {
    removeAccessToken();  // clears nxentra_access + nxentra_refresh

    delete (axiosClient.defaults.headers as any).common?.Authorization;
    delete (axiosClient.defaults.headers as any).Authorization;

    router.push('/login');
  };


  const handleCodeChange = (code: string, name: string) => {
    setAccountCode(code);
    setAccountName(name);

    const matched = accountList.find((a) => a.code === code);
    if (matched) {
      setAccountType(matched.type);
      setAccountStatus(matched.status);
      const index = accountList.findIndex((a) => a.code === code);
      setCurrentIndex(index);
      fetchAccountDefaults(code);
    } else {
      setAccountType('');
      setAccountStatus('');
      setCurrentIndex(null);
      setAccountDefaults([]);
      setDimSelections({});
      setDimChecks({});
    }
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();

    const payload = {
      code: accountCode,
      name: accountName,
      type: accountType,
      status: accountStatus,
    };

    try {
      const existing = accountList.find((a) => a.code === accountCode);
      if (existing) {
        await axiosClient.put(`/accounts/${accountCode}/`, payload, {
          headers: getAuthHeaders(),
        });
      } else {
        await axiosClient.post('/accounts/', payload, {
          headers: getAuthHeaders(),
        });
      }

      // Save dimension selections
      for (const dim of dimensions) {
        const isCoAType = dim.applies_to_account_types.length > 0;
        const currentDefault = accountDefaults.find(d => d.dimension === dim.id);

        if (isCoAType) {
          const selectedValueId = dimSelections[dim.id];
          if (selectedValueId) {
            await accountsService.setAnalysisDefault(accountCode, dim.id, parseInt(selectedValueId));
          } else if (currentDefault) {
            await accountsService.removeAnalysisDefault(accountCode, dim.id);
          }
        } else {
          const isChecked = dimChecks[dim.id];
          if (isChecked && !currentDefault) {
            // Enable: set with first available value
            const firstValue = dim.values?.[0];
            if (firstValue) {
              await accountsService.setAnalysisDefault(accountCode, dim.id, firstValue.id);
            }
          } else if (!isChecked && currentDefault) {
            // Disable: remove the default
            await accountsService.removeAnalysisDefault(accountCode, dim.id);
          }
        }
      }

      alert('Saved successfully!');
      setIsEditing(false);
      await fetchAccounts();
    } catch (error) {
      console.error('Save failed:', error);
      alert('Error saving account');
    }
  };

  const handleDelete = async () => {
    if (!accountCode) return alert('Please select an account to delete');
    if (!confirm(`Are you sure you want to delete account "${accountCode}"?`)) return;
    try {
      await axiosClient.delete(`/accounts/${accountCode}/`, {
        headers: getAuthHeaders(),
      });
      alert('Deleted successfully!');
      setAccountCode('');
      setAccountName('');
      setAccountType('');
      setAccountStatus('');
      setIsEditing(false);
      setCurrentIndex(null);
      await fetchAccounts();
    } catch (error) {
      console.error('Delete failed:', error);
      alert('Error deleting account');
    }
  };

  const handleNext = () => {
    if (accountList.length === 0) return;

    if (currentIndex === null || currentIndex === -1) {
      const first = accountList[0];
      setAccountCode(first.code);
      setAccountName(first.name);
      setAccountType(first.type);
      setAccountStatus(first.status);
      setCurrentIndex(0);
      fetchAccountDefaults(first.code);
    } else if (currentIndex < accountList.length - 1) {
      const next = accountList[currentIndex + 1];
      setAccountCode(next.code);
      setAccountName(next.name);
      setAccountType(next.type);
      setAccountStatus(next.status);
      setCurrentIndex(currentIndex + 1);
      fetchAccountDefaults(next.code);
    } else {
      alert('No more records');
    }
  };

  const handlePrevious = () => {
    if (accountList.length === 0) return;

    if (currentIndex === null || currentIndex === -1) {
      const first = accountList[0];
      setAccountCode(first.code);
      setAccountName(first.name);
      setAccountType(first.type);
      setAccountStatus(first.status);
      setCurrentIndex(0);
      fetchAccountDefaults(first.code);
    } else if (currentIndex > 0) {
      const prev = accountList[currentIndex - 1];
      setAccountCode(prev.code);
      setAccountName(prev.name);
      setAccountType(prev.type);
      setAccountStatus(prev.status);
      setCurrentIndex(currentIndex - 1);
      fetchAccountDefaults(prev.code);
    } else {
      alert('You are at the first record');
    }
  };

  const sortedAccounts = [...accountList].sort((a, b) =>
    a.code.localeCompare(b.code),
  );

  const selectOptions = sortedAccounts.map((a) => ({
    value: a.code,
    label: `${a.code} - ${a.name}`,
    name: a.name,
  }));

  return (
    <main className="min-h-screen bg-slate-900 text-slate-100 p-6 space-y-6">
      <div className="flex justify-between items-center border-b border-slate-700 pb-4 mb-6">
        <div>
          <span className="text-lg font-medium text-slate-300">
            Welcome, {userName}
          </span>
        </div>

        <div className="flex items-center space-x-4">
          <Link href="/profile" className="text-green-400 hover:text-green-300 transition-colors">
            My Profile
          </Link>
          <Button
            onClick={handleLogout}
            variant="ghost"
            className="text-red-400 hover:text-red-300 hover:bg-slate-800"
          >
            Logout
          </Button>
        </div>
      </div>

      <h1 className="text-3xl font-bold text-green-400">Chart of Accounts</h1>

      <div>
        <Link href="/accounting" className="text-green-400 hover:text-green-300 hover:underline font-medium">
          ← Back to Accounting
        </Link>
      </div>

      <form onSubmit={handleSave} className="space-y-6 max-w-xl">
        <div className="flex items-center">
          <label className="font-medium w-40 text-slate-300">Account Code</label>
          <div className="flex-1 text-slate-900">
            <SelectWithCreate
              value={accountCode}
              onChange={handleCodeChange}
              options={selectOptions}
              isDisabled={false}
            />
          </div>
        </div>

        <div className="flex items-center">
          <label className="font-medium w-40 text-slate-300">Account Name</label>
          <div className="flex-1 text-slate-900">
            <TextInput
              value={accountName}
              onChange={setAccountName}
              placeholder="Account Name"
              maxLength={30}
              disabled={false}
            />
          </div>
        </div>

        <div className="flex items-center">
          <label className="font-medium w-40 text-slate-300">Account Type</label>
          <div className="flex-1">
            <select
              value={accountType}
              onChange={(e) => setAccountType(e.target.value)}
              className="border border-gray-600 bg-slate-800 text-white rounded px-2 py-1 w-60 focus:border-green-500 focus:outline-none"
              disabled={false}
            >
              <option value="">Select Type</option>
              <option value="P&L">P&amp;L</option>
              <option value="BS">BS</option>
              <option value="Vendor">Vendor</option>
              <option value="Customer">Customer</option>
              <option value="Trader">Trader</option>
              <option value="Statistic">Statistic</option>
            </select>
          </div>
        </div>

        <div className="flex items-center">
          <label className="font-medium w-40 text-slate-300">Account Status</label>
          <div className="flex-1">
            <select
              value={accountStatus}
              onChange={(e) => setAccountStatus(e.target.value)}
              className="border border-gray-600 bg-slate-800 text-white rounded px-2 py-1 w-60 focus:border-green-500 focus:outline-none"
              disabled={false}
            >
              <option value="">Select Status</option>
              <option value="Open">Open</option>
              <option value="Closed">Closed</option>
            </select>
          </div>
        </div>

        {/* Analysis Dimensions */}
        {dimensions.length > 0 && (
          <div className="border-t border-slate-700 pt-4 mt-4 space-y-4">
            <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wide">Analysis Dimensions</h3>
            {dimensions.map((dim) => {
              const isCoAType = dim.applies_to_account_types.length > 0;

              if (isCoAType) {
                // Chart of Accounts type: dropdown to select one code
                return (
                  <div key={dim.id} className="flex items-center">
                    <label className="font-medium w-40 text-slate-300">{dim.name}</label>
                    <div className="flex-1">
                      <select
                        value={dimSelections[dim.id] || ''}
                        onChange={(e) => setDimSelections({ ...dimSelections, [dim.id]: e.target.value })}
                        className="border border-gray-600 bg-slate-800 text-white rounded px-2 py-1 w-60 focus:border-green-500 focus:outline-none"
                      >
                        <option value="">-- Select --</option>
                        {dim.values?.map((val) => (
                          <option key={val.id} value={val.id.toString()}>
                            {val.code} - {val.name}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                );
              } else {
                // Journal Entry type: checkbox
                return (
                  <div key={dim.id} className="flex items-center">
                    <label className="font-medium w-40 text-slate-300">{dim.name}</label>
                    <div className="flex-1">
                      <input
                        type="checkbox"
                        checked={dimChecks[dim.id] || false}
                        onChange={(e) => setDimChecks({ ...dimChecks, [dim.id]: e.target.checked })}
                        className="h-4 w-4 accent-green-500"
                      />
                    </div>
                  </div>
                );
              }
            })}
          </div>
        )}

        <div className="flex space-x-4 pt-6">
          <Button
            type="button"
            className="bg-yellow-500 hover:bg-yellow-600 text-slate-900 font-semibold"
            onClick={() => setIsEditing(true)}
          >
            Edit
          </Button>

          <Button
            type="submit"
            className="bg-green-600 hover:bg-green-700 text-white"
            disabled={false}
          >
            Save
          </Button>

          <Button
            type="button"
            className="bg-red-400 hover:bg-red-500 text-white border-none"
            onClick={handleDelete}
            disabled={false}
          >
            Delete
          </Button>
        </div>

        <div className="flex space-x-4 pt-6">
          <Button
            type="button"
            variant="outline"
            className="border-slate-500 text-slate-300 hover:bg-slate-800 hover:text-white"
            onClick={handlePrevious}
          >
            Previous
          </Button>
          <Button
            type="button"
            variant="outline"
            className="border-slate-500 text-slate-300 hover:bg-slate-800 hover:text-white"
            onClick={handleNext}
          >
            Next
          </Button>
        </div>
      </form>
    </main>
  );
}
