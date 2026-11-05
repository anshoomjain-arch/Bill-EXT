// Upload flow per pipeline: POST /jobs -> presigned S3 POST upload -> poll GET /jobs/{id} until done/error.
// With BILL_QWEN_API_URL set, the same file goes to both pipelines and the results are compared.
(function () {
  "use strict";

  const strip = (u) => (u || "").replace(/\/+$/, "");
  const RULES_API = strip(window.BILL_API_URL);
  const QWEN_API = strip(window.BILL_QWEN_API_URL);
  const DUAL = Boolean(window.BILL_QWEN_API_URL);
  const POLL_MS = window.BILL_POLL_MS || 5000;
  const GIVE_UP_MS = 20 * 60 * 1000;          // OCR Lambda timeout is 15 min
  const MAX_BYTES = 10 * 1024 * 1024;
  const TEXT_FIELDS = new Set(["Account Number", "Meter Number", "Mobile Number"]);

  const $ = (id) => document.getElementById(id);
  const form = $("upload-form"), fileInput = $("file"), submit = $("submit");
  const preview = $("preview"), results = $("results");
  let rules = null, qwen = null;              // {status, result | error, seconds}

  function setStatus(el, text, { busy = false, error = false } = {}) {
    el.className = error ? "error" : "";
    el.textContent = "";
    if (busy) {
      const s = document.createElement("span");
      s.className = "spinner";
      s.setAttribute("aria-hidden", "true");
      el.appendChild(s);
    }
    el.appendChild(document.createTextNode(text));
  }

  fileInput.addEventListener("change", () => {
    const f = fileInput.files[0];
    submit.disabled = true;
    preview.hidden = true;
    if (!f) return;
    $("file-label").textContent = f.name;
    if (!["image/png", "image/jpeg"].includes(f.type)) return setStatus($("status"), "Please choose a PNG or JPEG image.", { error: true });
    if (f.size > MAX_BYTES) return setStatus($("status"), "The image is larger than 10 MB.", { error: true });
    preview.src = URL.createObjectURL(f);
    preview.hidden = false;
    submit.disabled = false;
    setStatus($("status"), "");
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = fileInput.files[0];
    if (!f) return;
    submit.disabled = true;
    fileInput.disabled = true;
    results.hidden = true;
    rules = qwen = null;
    setStatus($("status"), "");
    $("pipelines").hidden = !DUAL;
    const rulesEl = DUAL ? $("status-rules") : $("status");
    const runs = [run(RULES_API, f, rulesEl).then((r) => { rules = r; render(); })];
    if (DUAL) runs.push(run(QWEN_API, f, $("status-qwen")).then((r) => { qwen = r; render(); }));
    await Promise.all(runs);
    fileInput.disabled = false;
    submit.disabled = !fileInput.files[0];
  });

  // one pipeline: never throws, returns {status: "done"|"error", ...}
  async function run(api, f, el) {
    let timer = null;
    try {
      setStatus(el, "Starting…", { busy: true });
      const job = await call(api, "POST", "/jobs", { content_type: f.type });

      setStatus(el, "Uploading…", { busy: true });
      const fd = new FormData();
      Object.entries(job.upload.fields).forEach(([k, v]) => fd.append(k, v));
      fd.append("file", f);                     // must be the last field for S3
      const up = await fetch(job.upload.url, { method: "POST", body: fd });
      if (!up.ok) throw new Error(`Upload failed (${up.status})`);

      const started = Date.now();
      const tick = () => {
        const s = Math.round((Date.now() - started) / 1000);
        setStatus(el, `Reading the bill… ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} ` +
                      "(usually about 5 minutes — you can leave this page open)", { busy: true });
      };
      tick();
      timer = setInterval(tick, 1000);
      const out = await poll(api, job.job_id);
      clearInterval(timer);
      if (out.status === "error") throw new Error(`Could not process the bill: ${out.error}`);
      setStatus(el, `Done in ${out.seconds != null ? Math.round(out.seconds) + " s" : "a moment"}.`);
      return out;
    } catch (err) {
      clearInterval(timer);
      setStatus(el, err.message || String(err), { error: true });
      return { status: "error", error: err.message || String(err) };
    }
  }

  async function call(api, method, path, body) {
    const r = await fetch(api + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
    return data;
  }

  async function poll(api, jobId) {
    const started = Date.now();
    while (Date.now() - started < GIVE_UP_MS) {
      await new Promise((r) => setTimeout(r, POLL_MS));
      const out = await call(api, "GET", `/jobs/${jobId}`);
      if (out.status !== "processing") return out;
    }
    throw new Error("Timed out waiting for the result. Please try again.");
  }

  // ---------------------------------------------------------------- rendering
  const fields = (p) => (p && p.status === "done" ? p.result.fields : null);
  const show = (field, v) => (v === null || v === undefined || v === "" ? null
    : TEXT_FIELDS.has(field) ? String(v) : v);
  const same = (a, b) => {
    if (a === null || a === undefined) return b === null || b === undefined;
    if (typeof a === "number" || typeof b === "number") return Number(a) === Number(b);
    return String(a).trim().toLowerCase() === String(b).trim().toLowerCase();
  };

  function cell(tr, text, cls) {
    const td = document.createElement("td");
    if (text === null || text === undefined || text === "") {
      td.textContent = "—";
      td.className = "empty";
    } else {
      td.textContent = typeof text === "number" ? text.toLocaleString("en-IN", { maximumFractionDigits: 6 }) : text;
      if (cls) td.className = cls;
    }
    tr.appendChild(td);
    return td;
  }

  function header(cols) {
    const thead = document.querySelector("#fields thead");
    thead.textContent = "";
    const tr = thead.insertRow();
    cols.forEach((c) => { const th = document.createElement("th"); th.textContent = c; tr.appendChild(th); });
  }

  function render() {
    const fr = fields(rules), fq = fields(qwen);
    const base = fr || fq;
    if (!base) return;                          // nothing finished successfully yet

    const tb = document.querySelector("#fields tbody");
    tb.textContent = "";
    if (!DUAL) {
      header(["Section", "Field Name", "Extracted Value"]);
      fr.forEach((f) => {
        const tr = tb.insertRow();
        cell(tr, f.section);
        cell(tr, f.field);
        cell(tr, show(f.field, f.value));
      });
      $("summary").textContent = `${rules.result.summary.values_filled} values filled`;
    } else {
      header(["Section", "Field Name", "Rules (pipeline 1)", "Qwen (pipeline 2)", "Match"]);
      const pending = (p) => (p === null ? "…" : null);
      let matches = 0;
      base.forEach((f, i) => {
        const a = fr ? show(f.field, fr[i].value) : null;
        const b = fq ? show(f.field, fq[i].value) : null;
        const tr = tb.insertRow();
        cell(tr, f.section);
        cell(tr, f.field);
        cell(tr, fr ? a : pending(rules));
        cell(tr, fq ? b : pending(qwen));
        if (fr && fq) {
          const ok = same(a, b);
          matches += ok;
          cell(tr, ok ? "✓" : "✗", ok ? "match" : "differ");
          if (!ok) tr.className = "differ-row";
        } else {
          cell(tr, null);
        }
      });
      const parts = [];
      if (fr) parts.push(`Rules: ${rules.result.summary.values_filled} values filled`);
      if (fq) parts.push(`Qwen: ${qwen.result.summary.values_filled} values filled`);
      if (fr && fq) parts.push(`same value on ${matches}/${base.length} fields`);
      $("summary").textContent = parts.join(" · ");
      renderLlm();
    }
    results.hidden = false;
  }

  function renderLlm() {
    const box = $("llm");
    if (!fields(qwen) || !qwen.result.llm) { box.hidden = true; return; }
    const u = qwen.result.llm;
    const n = (v) => (v == null ? "—" : Number(v).toLocaleString("en-IN"));
    const secs = (v) => (v == null ? "—" : `${Number(v).toFixed(1)} s`);
    $("llm-model").textContent = u.model_id || "—";
    $("llm-calls").textContent = u.calls == null ? "1"
      : `${u.calls} (majority vote)${u.usable_answers != null && u.usable_answers < u.calls ? `, ${u.usable_answers} usable` : ""}`;
    $("llm-in").textContent = n(u.input_tokens);
    $("llm-out").textContent = n(u.output_tokens);
    $("llm-total").textContent = n(u.total_tokens);
    $("llm-time").textContent = u.latency_ms != null ? secs(u.latency_ms / 1000) : secs(u.seconds);
    $("llm-ocr").textContent = secs(qwen.result.ocr_seconds);
    box.hidden = false;
  }

  $("download").addEventListener("click", () => {
    const fr = fields(rules), fq = fields(qwen), base = fr || fq;
    if (!base) return;
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const head = DUAL ? ["Section", "Field Name", "Rules (pipeline 1)", "Qwen (pipeline 2)"]
                      : ["Section", "Field Name", "Extracted Value"];
    const lines = [head.map(q).join(",")].concat(base.map((f, i) => {
      const row = [f.section, f.field, fr ? fr[i].value : ""];
      if (DUAL) row.push(fq ? fq[i].value : "");
      return row.map(q).join(",");
    }));
    const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "bill_output.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  });
})();
