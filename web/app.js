"use strict";
const byId = (id) => document.getElementById(id);
let latest = null;
let loading = false;
const date = (seconds) =>
  new Date(seconds * 1000).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
const escapeHTML = (text) =>
  String(text ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
const duration = (seconds) =>
  seconds < 60
    ? `${seconds}s`
    : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;

function render(data) {
  latest = data;
  const services = data.services;
  const up = services.filter((s) => s.status === "up").length;
  const open = services.filter((s) => s.incident_since !== null).length;
  const history = services.flatMap((s) => s.history);
  const successes = history.filter((h) => h.ok);
  byId("mode").textContent =
    data.mode === "demo"
      ? "Local demo"
      : data.mode === "aws"
        ? "AWS workspace"
        : "Local monitor";
  byId("healthy").textContent = up;
  byId("healthy-total").textContent = `/ ${services.length} monitored`;
  byId("uptime").textContent = history.length
    ? ((successes.length / history.length) * 100).toFixed(2)
    : "—";
  byId("latency").textContent = successes.length
    ? Math.round(
        successes.reduce((n, h) => n + h.latency_ms, 0) / successes.length,
      )
    : "—";
  byId("open").textContent = open;
  const allUp = services.length > 0 && up === services.length;
  byId("health-banner").classList.toggle("attention", !allUp);
  document.querySelector(".status-symbol").textContent = allUp ? "✓" : "!";
  byId("health-title").textContent = allUp
    ? "All services are operational"
    : "Some services need attention";
  byId("health-detail").textContent = allUp
    ? "All configured services passed their latest check."
    : "Review failed, pending or stale checks below.";
  byId("updated").textContent =
    `Updated ${new Date(data.generated_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
  byId("service-list").innerHTML = services
    .map(
      (s) =>
        `<article class="service"><div><div class="service-title"><i class="${escapeHTML(s.status)}"></i>${escapeHTML(s.name)}</div><p class="service-url" title="${escapeHTML(s.url)}">${escapeHTML(s.url)}</p>${s.error ? `<div class="service-error">${escapeHTML(s.error)}</div>` : ""}</div><div class="service-history"><div class="history" aria-label="${s.history.filter((h) => h.ok).length} successful checks out of ${s.history.length}">${s.history.map((h) => `<span class="${h.ok ? "up" : "down"}" title="${escapeHTML(date(h.checked_at))} · ${h.ok ? "OK" : escapeHTML(h.error)} · ${h.latency_ms} ms"></span>`).join("")}</div><div class="history-label"><span>${s.history.length ? escapeHTML(date(s.history[0].checked_at)) : "No checks yet"}</span><span>${s.uptime_percent ?? "—"}% sampled uptime</span></div></div><div class="service-number">${s.latency_ms ?? "—"} ms<small>Latest response</small></div><div class="service-right"><span class="badge ${escapeHTML(s.status)}">${escapeHTML(s.status)}</span><span class="service-number"><small>${s.checked_at ? escapeHTML(new Date(s.checked_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })) : "Awaiting check"}</small></span></div></article>`,
    )
    .join("");
  const selected = byId("chart-service").value;
  byId("chart-service").innerHTML = services
    .map(
      (s) =>
        `<option value="${escapeHTML(s.id)}">${escapeHTML(s.name)}</option>`,
    )
    .join("");
  if (services.some((s) => s.id === selected))
    byId("chart-service").value = selected;
  drawChart();
  const events = services
    .flatMap((s) => s.events.map((e) => ({ ...e, name: s.name })))
    .sort((a, b) => b.at - a.at)
    .slice(0, 5);
  byId("event-count").textContent = `${events.length} recent events`;
  byId("events").innerHTML = events.length
    ? events
        .map(
          (e) =>
            `<div class="event"><span class="event-icon ${e.type}" aria-hidden="true">${e.type === "opened" ? "!" : "✓"}</span><div><h3>${escapeHTML(e.name)} ${e.type === "opened" ? "incident opened" : "recovered"}</h3><p>${e.type === "opened" ? "Two consecutive checks failed." : `Confirmed incident lasted ${duration(e.duration_seconds)}.`}</p></div><time datetime="${new Date(e.at * 1000).toISOString()}">${escapeHTML(date(e.at))}</time></div>`,
        )
        .join("")
    : '<p class="empty">No incidents in the available history.</p>';
  byId("demo-controls").hidden = data.mode !== "demo";
  byId("footer-note").textContent =
    data.mode === "demo"
      ? "Synthetic seed history · live local checks every 10 seconds"
      : "Checks every minute · dashboard refreshes every 10 seconds";
}

function drawChart() {
  if (!latest) return;
  const service = latest.services.find(
    (s) => s.id === byId("chart-service").value,
  );
  const samples = service ? service.history.slice(-30) : [];
  if (!samples.length) {
    byId("chart").innerHTML = '<text x="210" y="85">Waiting for checks</text>';
    return;
  }
  const max = Math.max(100, ...samples.map((h) => h.latency_ms)) * 1.2;
  const points = samples.map((h, i) => [
    40 + (i * 550) / Math.max(samples.length - 1, 1),
    150 - (h.latency_ms / max) * 130,
  ]);
  const coords = points.map((p) => p.join(",")).join(" ");
  const grid = [0, 1, 2]
    .map((i) => {
      const y = 20 + i * 65;
      return `<line class="grid" x1="40" x2="590" y1="${y}" y2="${y}"/><text x="2" y="${y + 4}">${Math.round(max * (1 - i / 2))}</text>`;
    })
    .join("");
  byId("chart").innerHTML =
    `${grid}<polygon class="area" points="40,150 ${coords} ${points.at(-1)[0]},150"/><polyline class="line" points="${coords}"/>${samples.map((h, i) => (h.ok ? "" : `<circle class="failure" cx="${points[i][0]}" cy="${points[i][1]}" r="4"><title>Failed: ${escapeHTML(h.error)}</title></circle>`)).join("")}`;
}

async function refresh() {
  if (loading) return;
  loading = true;
  byId("refresh").disabled = true;
  try {
    const response = await fetch("/api/status");
    if (!response.ok)
      throw new Error(`Dashboard request failed (${response.status}).`);
    render(await response.json());
    byId("error").hidden = true;
  } catch (error) {
    byId("error").hidden = false;
    byId("error").textContent =
      `${error.message} Displayed data may be old. Try refreshing.`;
    byId("health-title").textContent = "Monitoring data is unavailable";
    byId("health-banner").classList.add("attention");
  } finally {
    loading = false;
    byId("refresh").disabled = false;
  }
}
async function demoAction(action, button) {
  button.disabled = true;
  try {
    const response = await fetch(`/api/demo/${action}`, { method: "POST" });
    if (!response.ok)
      throw new Error(
        "Demo action failed. Open this page using http://127.0.0.1:8080 (or your configured port).",
      );
    const result = await response.json();
    if (action === "toggle") {
      byId("toggle").textContent = result.failing
        ? "Restore Billing"
        : "Fail Billing";
      byId("demo-message").textContent = result.failing
        ? "Billing now returns HTTP 503. Two failed checks will open an incident."
        : "Billing is healthy again. The next successful check will resolve its incident.";
    } else
      byId("demo-message").textContent =
        "Check complete. Checks in the same second are deduplicated.";
    await refresh();
  } catch (error) {
    byId("demo-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}
byId("chart-service").addEventListener("change", drawChart);
byId("refresh").addEventListener("click", refresh);
byId("toggle").addEventListener("click", (event) =>
  demoAction("toggle", event.currentTarget),
);
byId("check").addEventListener("click", (event) =>
  demoAction("check", event.currentTarget),
);
refresh();
setInterval(refresh, 10000);
