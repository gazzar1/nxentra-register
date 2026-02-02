"use client";

import { useEffect, useState } from "react";

type User = {
  id: number;
  email: string;
  is_active: boolean;
};

const API_BASE = "http://127.0.0.1:8000";

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // selected user for edit
  const [selectedUser, setSelectedUser] = useState<User | null>(null);
  const [editEmail, setEditEmail] = useState("");
  const [editActive, setEditActive] = useState(true);

  // admin reset password
  const [resetPassword, setResetPassword] = useState("");

  // add new user
  const [newEmail, setNewEmail] = useState("");
  const [newActive, setNewActive] = useState(true);
  const [newPassword, setNewPassword] = useState("");

  // ---------- helpers ----------
  async function loadUsers() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/users/`, {
        credentials: "include", // use session auth cookie
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      const data = await res.json();
      setUsers(data);
    } catch (err: any) {
      console.error(err);
      setError(err.message ?? "Error loading users");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadUsers();
  }, []);

  // when you click a row
  function handleRowClick(user: User) {
    setSelectedUser(user);
    setEditEmail(user.email ?? "");
    setEditActive(user.is_active);
    setResetPassword("");
  }

  async function handleSave() {
    if (!selectedUser) return;

    try {
      const res = await fetch(
        `${API_BASE}/api/users/${selectedUser.id}/`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
          },
          credentials: "include",
          body: JSON.stringify({
            email: editEmail,
            is_active: editActive,
          }),
        }
      );

      if (!res.ok) {
        alert(`Error saving: HTTP ${res.status}`);
        return;
      }

      await loadUsers();
      const updated = users.find((u) => u.id === selectedUser.id);
      if (updated) {
        setSelectedUser(updated);
      }
    } catch (err) {
      console.error(err);
      alert("Error saving (network)");
    }
  }

  async function handleDelete() {
    if (!selectedUser) return;

    if (!window.confirm(`Delete user #${selectedUser.id}?`)) return;

    try {
      const res = await fetch(
        `${API_BASE}/api/users/${selectedUser.id}/`,
        {
          method: "DELETE",
          credentials: "include",
        }
      );

      if (!res.ok) {
        alert(`Error deleting: HTTP ${res.status}`);
        return;
      }

      setSelectedUser(null);
      setEditEmail("");
      setResetPassword("");
      await loadUsers();
    } catch (err) {
      console.error(err);
      alert("Error deleting (network)");
    }
  }

  async function handleAdd() {
    try {
      const res = await fetch(`${API_BASE}/api/users/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "include",
        body: JSON.stringify({
          email: newEmail,
          is_active: newActive,
          // password is *not* handled yet in serializer, we'll wire later
        }),
      });

      if (!res.ok) {
        const text = await res.text();
        alert(`Error adding user: HTTP ${res.status}`);
        return;
      }

      setNewEmail("");
      setNewPassword("");
      setNewActive(true);
      await loadUsers();
    } catch (err) {
      console.error(err);
      alert("Error adding user (network)");
    }
  }

  async function handleSetPassword() {
    if (!selectedUser) return;
    if (!resetPassword) {
      alert("Enter a new password first.");
      return;
    }

    try {
      const res = await fetch(
        `${API_BASE}/api/users/${selectedUser.id}/set-password/`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          credentials: "include",
          body: JSON.stringify({ password: resetPassword }),
        }
      );

      if (!res.ok) {
        alert(`Error setting password: HTTP ${res.status}`);
        return;
      }

      alert("Password changed.");
      setResetPassword("");
    } catch (err) {
      console.error(err);
      alert("Error setting password (network)");
    }
  }

  // ---------- render ----------
  return (
    <div
      style={{
        minHeight: "100vh",
        backgroundColor: "#050712",
        color: "white",
        padding: "20px",
        fontFamily: "sans-serif",
      }}
    >
      <h1>Users list</h1>

      {loading && <p>Loading…</p>}
      {error && <p style={{ color: "red" }}>Error: {error}</p>}

      <table
        style={{
          borderCollapse: "collapse",
          marginBottom: "20px",
        }}
      >
        <thead>
          <tr>
            <th style={thStyle}>ID</th>
            <th style={thStyle}>Username</th>
            <th style={thStyle}>Email</th>
            <th style={thStyle}>Active</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr
              key={u.id}
              onClick={() => handleRowClick(u)}
              style={{
                cursor: "pointer",
                backgroundColor:
                  selectedUser && selectedUser.id === u.id
                    ? "#333a55"
                    : "transparent",
              }}
            >
              <td style={tdStyle}>{u.id}</td>
              <td style={tdStyle}>{u.email}</td>
              <td style={tdStyle}>{u.email}</td>
              <td style={tdStyle}>{u.is_active ? "Yes" : "No"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* EDIT FORM */}
      {selectedUser && (
        <div style={{ marginBottom: "30px" }}>
          <h3>Edit user #{selectedUser.id}</h3>
          <div style={{ marginBottom: "8px" }}>
            <label>
              Email:{" "}
              <input
                type="email"
                value={editEmail}
                onChange={(e) => setEditEmail(e.target.value)}
                style={inputStyle}
              />
            </label>
          </div>
          <div style={{ marginBottom: "8px" }}>
            <label>
              Active:{" "}
              <input
                type="checkbox"
                checked={editActive}
                onChange={(e) => setEditActive(e.target.checked)}
              />
            </label>
          </div>
          <button onClick={handleSave}>Save</button>
          <button
            onClick={handleDelete}
            style={{ marginLeft: "10px", color: "red" }}
          >
            Delete
          </button>

          <div style={{ marginTop: "20px" }}>
            <h4>Admin Reset Password</h4>
            <input
              type="password"
              placeholder="New password"
              value={resetPassword}
              onChange={(e) => setResetPassword(e.target.value)}
              style={inputStyle}
            />
            <button onClick={handleSetPassword} style={{ marginLeft: "10px" }}>
              Set password
            </button>
          </div>
        </div>
      )}

      {/* ADD FORM */}
      <div>
        <h3>Add new user</h3>
        <div style={{ marginBottom: "8px" }}>
          <label>
            Email:{" "}
            <input
              type="email"
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
              style={inputStyle}
              autoComplete="off"
            />
          </label>
        </div>
        <div style={{ marginBottom: "8px" }}>
          <label>
            Active:{" "}
            <input
              type="checkbox"
              checked={newActive}
              onChange={(e) => setNewActive(e.target.checked)}
            />
          </label>
        </div>
        <div style={{ marginBottom: "8px" }}>
          <label>
            (Optional) Password (not yet enforced):{" "}
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              style={inputStyle}
              autoComplete="new-password"
            />
          </label>
        </div>
        <button onClick={handleAdd}>Add user</button>
      </div>
    </div>
  );
}

const thStyle: React.CSSProperties = {
  border: "1px solid #777",
  padding: "6px 10px",
};

const tdStyle: React.CSSProperties = {
  border: "1px solid #555",
  padding: "4px 10px",
};

const inputStyle: React.CSSProperties = {
  backgroundColor: "white",
  color: "black",
  padding: "4px 6px",
  borderRadius: 4,
  border: "1px solid #999",
};
