import { useEffect, useState } from "react";

type Account = {
  code: string;
  name: string;
};

export default function GazzarPage() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [selectedCode, setSelectedCode] = useState("");
  const [accountName, setAccountName] = useState("");

  // fields for add/edit
  const [newCode, setNewCode] = useState("");
  const [newName, setNewName] = useState("");

  /* useEffect(() => {
    // initial dummy data
    setAccounts([
      { code: "10001", name: "Cash" },
      { code: "20001", name: "Sales" },
      { code: "30001", name: "Inventory" },
    ]);
  }, []); */

useEffect(() => {
    // fetch accounts from backend
    async function loadAccounts() {
        const res = await fetch("http://127.0.0.1:8000/api/gazzar/accounts/");
        const data = await res.json();
        setAccounts(data);
    }
    loadAccounts();
}, []);






  // select account from dropdown
  const handleSelect = (event: any) => {
    const code = event.target.value;
    setSelectedCode(code);

    const found = accounts.find((a) => a.code === code);
    setAccountName(found ? found.name : "");
  };

  // Add new account
  const handleAdd = () => {
    if (!newCode || !newName) {
      alert("Please fill both fields.");
      return;
    }

    // prevent duplicates
    if (accounts.some((a) => a.code === newCode)) {
      alert("Code already exists.");
      return;
    }

    setAccounts([...accounts, { code: newCode, name: newName }]);
    setNewCode("");
    setNewName("");
  };

  // Update selected account
  const handleUpdate = () => {
    if (!selectedCode) {
      alert("Choose an account first.");
      return;
    }

    const updated = accounts.map((a) =>
      a.code === selectedCode ? { code: selectedCode, name: accountName } : a
    );

    setAccounts(updated);
    alert("Updated successfully");
  };

  // Delete selected account
  const handleDelete = () => {
    if (!selectedCode) {
      alert("Choose an account first.");
      return;
    }

    setAccounts(accounts.filter((a) => a.code !== selectedCode));

    // reset UI
    setSelectedCode("");
    setAccountName("");
  };

  return (
  <div
    style={{
      padding: 20,
      backgroundColor: "hsla(232, 22%, 56%, 1.00)",   // خلفية بيضا
      color: "#000000",             // نص أسود واضح
      minHeight: "100vh",
      fontSize: 16,                 // تكبير بسيط للخط
      lineHeight: 1.5,
    }}
  >
    <h1>Gazzar CRUD Test Page</h1>

    <label>
      Select Account:
      <select value={selectedCode} onChange={handleSelect}>
        <option value="">-- choose --</option>
        {accounts.map((a) => (
          <option key={a.code} value={a.code}>
            {a.code}
          </option>
        ))}
      </select>
    </label>

    <div style={{ marginTop: 10 }}>
      <label>
        Account name:
        <input
          type="text"
          value={accountName}
          onChange={(e) => setAccountName(e.target.value)}
          style={{ width: 200 }}
        />
      </label>
    </div>

    <div style={{ marginTop: 10 }}>
      <button onClick={handleUpdate}>Update</button>
      <button onClick={handleDelete} style={{ marginLeft: 10 }}>
        Delete
      </button>
    </div>

    <hr style={{ margin: "30px 0" }} />

    <h2>Add new account</h2>
    <label>
      New code:
      <input
        type="text"
        value={newCode}
        onChange={(e) => setNewCode(e.target.value)}
        style={{ width: 200 }}
      />
    </label>

    <br />

    <label>
      New name:
      <input
        type="text"
        value={newName}
        onChange={(e) => setNewName(e.target.value)}
        style={{ width: 200 }}
      />
    </label>

    <br />

    <button style={{ marginTop: 10 }} onClick={handleAdd}>
      Add
    </button>

    <hr />
    <h2>Accounts list:</h2>
    <pre
      style={{
        backgroundColor: "#eee",
        padding: 10,
        borderRadius: 4,
        maxWidth: 400,
        overflowX: "auto",
      }}
    >
      {JSON.stringify(accounts, null, 2)}
    </pre>
  </div>
);
}

