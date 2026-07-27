"use strict";

let latestRows = [];

async function api(name, payload) {
  const res = await fetch("/api/" + name, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  if (res.status === 401) { location.reload(); return null; }  // session expired
  return res.json();
}

function fmt(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toISOString().slice(0, 16).replace("T", " ");
}

async function load() {
  const empty = document.getElementById("empty");
  const tbody = document.getElementById("rows");
  empty.textContent = "loading…";
  empty.hidden = false;
  tbody.replaceChildren();

  const [s, list] = await Promise.all([api("stats"), api("signups")]);
  if (!s || !list) return;

  document.getElementById("viewer").textContent = s.viewer || "";
  document.getElementById("total").textContent = s.total;
  document.getElementById("week").textContent = s.last_7_days;
  document.getElementById("latest").textContent = fmt(s.latest);

  latestRows = list.rows;
  for (const r of latestRows) {
    const tr = document.createElement("tr");
    const email = document.createElement("td");
    email.textContent = r.email;
    const when = document.createElement("td");
    when.textContent = fmt(r.joined_at);
    tr.append(email, when);
    tbody.append(tr);
  }
  empty.hidden = latestRows.length > 0;
  if (!latestRows.length) empty.textContent = "nobody yet — go share the landing page";
}

function csv() {
  const quote = (v) => `"${String(v).replace(/"/g, '""')}"`;
  const body = [["email", "joined_at"]]
    .concat(latestRows.map((r) => [r.email, r.joined_at]))
    .map((row) => row.map(quote).join(","))
    .join("\n");
  const url = URL.createObjectURL(new Blob([body], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = "signups.csv";
  a.click();
  URL.revokeObjectURL(url);
}

document.getElementById("refresh").addEventListener("click", load);
document.getElementById("csv").addEventListener("click", csv);
load();
