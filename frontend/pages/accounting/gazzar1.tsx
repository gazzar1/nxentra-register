import { useState } from "react";

export default function TestPage() {
  const [name, setName] = useState("");

  return (
    <div>
      <h1>Test Page</h1>
      <input
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        />
        <button onClick={() => alert(name)}>Show Name</button>
        <button onClick={() => setName("")}>Clear</button>

    </div>
  );
}
