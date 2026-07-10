"use strict";
const out = document.getElementById("out");

async function api(name, payload) {
  const res = await fetch("/api/" + name, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  if (res.status === 401) { location.reload(); return; }  // session expired
  out.textContent = JSON.stringify(await res.json(), null, 2);
}

document.getElementById("now").addEventListener("click", () => api("now"));
document.getElementById("echo").addEventListener("click", () =>
  api("echo", { message: document.getElementById("msg").value }));
