// In-browser stand-in for the app's backend, used only by the hosted demo.
// It answers the same /api/plan routes as app/routers/api.py and ports the clash
// and auto-fill rules from app/scheduler.py. Nothing leaves the browser.
(() => {
  const SEED = window.DEMO_SEED;
  const KEY = "sprint-demo-v1";
  const MIN = 60000;
  const { prefs, categories } = SEED;

  const load = () => {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || "null");
      if (saved && saved.seedVersion === SEED.seedVersion) return saved;
    } catch (e) {}
    return { seedVersion: SEED.seedVersion, items: structuredClone(SEED.items), nextId: 1000, imported: false };
  };
  let state = load();
  const save = () => {
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {}
  };
  window.resetDemo = () => {
    try { localStorage.removeItem(KEY); } catch (e) {}
    location.reload();
  };

  // Scheduling rules (same as app/scheduler.py)

  const travel = (a, b) => {
    a = (a || "").trim().toLowerCase();
    b = (b || "").trim().toLowerCase();
    if (!a || !b || a === b) return 0;
    const t = prefs.travel_minutes;
    return t[`${a}|${b}`] ?? t[`${b}|${a}`] ?? 0;
  };
  const gap = (a, b) => Math.max(prefs.buffer_min, travel(a.loc, b.loc)) * MIN;
  const clashes = (a, b) => a.s < b.e + gap(a, b) && b.s < a.e + gap(a, b);
  const slotOf = (i) => {
    const s = Date.parse(i.start);
    return { s, e: s + i.duration_min * MIN, loc: i.location, title: i.title, id: i.id, cat: i.category };
  };
  const busySlots = () =>
    SEED.busy.filter((b) => !b.allDay).map((b) => ({ s: Date.parse(b.start), e: Date.parse(b.end), loc: b.location || "", title: b.title }));
  const hm = (v) => v.split(":").map(Number);
  const at = (day, v) => {
    const d = new Date(day);
    const [h, m] = hm(v);
    d.setHours(h, m, 0, 0);
    return d.getTime();
  };

  function describe(other, cand) {
    const name = other.title || "another block";
    if (cand.s < other.e && other.s < cand.e) return `Overlaps ${name}`;
    const t = travel(cand.loc, other.loc);
    return t ? `Needs ${t} min travel from/to ${name}` : `Less than ${prefs.buffer_min} min buffer next to ${name}`;
  }

  function issues() {
    const out = {};
    const add = (id, level, message) => (out[id] = out[id] || []).push({ level, message });
    const slots = state.items.filter((i) => i.start).map(slotOf);
    const busy = busySlots();
    slots.forEach((item, idx) => {
      for (const other of [...busy, ...slots.slice(idx + 1)]) {
        if (clashes(item, other)) {
          add(item.id, "clash", describe(other, item));
          if (other.id) add(other.id, "clash", describe(item, other));
        }
      }
      const start = new Date(item.s);
      const end = new Date(item.e);
      if (item.s < at(start, prefs.day_start) || item.e > at(start, prefs.day_end) || end.getDate() !== start.getDate())
        add(item.id, "warning", "Outside your day hours");
    });
    return out;
  }

  function autofill() {
    const weekStart = new Date(`${SEED.week}T00:00:00`);
    const days = [...Array(7)].map((_, d) => new Date(weekStart.getTime() + d * 86400000));
    const occupied = [...busySlots(), ...state.items.filter((i) => i.start).map(slotOf)];
    const load = {};
    occupied.filter((o) => o.id).forEach((o) => {
      const k = `${new Date(o.s).toDateString()}|${o.cat}`;
      load[k] = (load[k] || 0) + (o.e - o.s) / MIN;
    });
    const now = Date.now();
    const todo = state.items.filter((i) => !i.start).sort((a, b) => a.priority - b.priority || b.duration_min - a.duration_min);
    let placed = 0;
    for (const item of todo) {
      const len = item.duration_min * MIN;
      const order = [...days].sort((a, b) => (load[`${a.toDateString()}|${item.category}`] || 0) - (load[`${b.toDateString()}|${item.category}`] || 0) || a - b);
      const windowsFor = (day) => {
        const full = [at(day, prefs.day_start), at(day, prefs.day_end)];
        const pw = prefs.preferred_windows[item.category];
        return pw ? [[at(day, pw[0]), at(day, pw[1])], full] : [full];
      };
      let start = null;
      for (const pass of [0, 1]) {
        for (const day of order) {
          const w = windowsFor(day)[pass];
          if (!w) continue;
          for (let t = w[0]; t + len <= w[1]; t += 15 * MIN) {
            const cand = { s: t, e: t + len, loc: item.location };
            if (t >= now && !occupied.some((o) => clashes(cand, o))) { start = t; break; }
          }
          if (start !== null) break;
        }
        if (start !== null) break;
      }
      if (start === null) continue;
      item.start = new Date(start).toISOString();
      item.dirty = true;
      occupied.push({ s: start, e: start + len, loc: item.location, id: item.id, cat: item.category });
      const k = `${new Date(start).toDateString()}|${item.category}`;
      load[k] = (load[k] || 0) + item.duration_min;
      placed++;
    }
    return placed;
  }

  // API

  const goalTitle = (id) => SEED.goals.find((g) => g.id === id)?.title || "";
  const iso = (ms) => new Date(ms).toISOString();
  const itemJson = (i, iss) => ({
    ...i,
    color: categories[i.category]?.color || "#616161",
    end: i.start ? iso(Date.parse(i.start) + i.duration_min * MIN) : null,
    goal_title: goalTitle(i.goal_id),
    committed: !!i.committed && !i.dirty,
    issues: (iss || {})[i.id] || [],
  });

  function apply(item, b) {
    if (b.title !== undefined) {
      if (!String(b.title).trim()) throw [400, "Title is required"];
      item.title = String(b.title).trim();
    }
    if (b.kind) item.kind = b.kind;
    if (b.category) item.category = b.category;
    if (b.duration_min !== undefined) {
      if (!(b.duration_min >= 5 && b.duration_min <= 1440)) throw [400, "Duration must be between 5 minutes and 24 hours"];
      item.duration_min = b.duration_min;
    }
    if (b.unschedule) item.start = null;
    else if (b.start) item.start = new Date(b.start).toISOString();
    if (b.location !== undefined) item.location = String(b.location).trim().toLowerCase();
    if (b.priority) item.priority = Math.min(4, Math.max(1, b.priority));
    if (b.clear_goal) item.goal_id = null;
    else if (b.goal_id) item.goal_id = b.goal_id;
    if (b.notes !== undefined) item.notes = b.notes;
    item.dirty = true;
  }

  function route(method, path, body) {
    const find = (id) => {
      const it = state.items.find((i) => i.id === Number(id));
      if (!it) throw [404, "Not found"];
      return it;
    };
    let m;
    if (method === "GET" && path === "/api/plan") {
      const iss = issues();
      const hours = {};
      state.items.filter((i) => i.start).forEach((i) => (hours[i.category] = Math.round(((hours[i.category] || 0) + i.duration_min / 60) * 100) / 100));
      return {
        week: SEED.week,
        items: state.items.map((i) => itemJson(i, iss)),
        busy: SEED.busy,
        errors: ["Demo with sample data. Changes stay in this browser and nothing is sent to Google Calendar or Todoist."],
        summary: { scheduled_hours: hours, backlog: state.items.filter((i) => !i.start).length },
      };
    }
    if (method === "POST" && path === "/api/plan/items") {
      const item = { id: state.nextId++, title: "", kind: "task", category: "school", duration_min: 60, start: null, location: "", priority: 4, goal_id: null, notes: "", origin: "app" };
      apply(item, { title: body.title || "", ...body });
      state.items.push(item);
      return itemJson(item);
    }
    if ((m = path.match(/^\/api\/plan\/items\/(\d+)$/))) {
      const item = find(m[1]);
      if (method === "PATCH") { apply(item, body); return itemJson(item); }
      if (method === "DELETE") { state.items = state.items.filter((i) => i !== item); return { errors: [] }; }
    }
    if ((m = path.match(/^\/api\/plan\/items\/(\d+)\/duplicate$/))) {
      const src = find(m[1]);
      const copy = { ...src, id: state.nextId++, start: null, committed: false, dirty: true };
      state.items.push(copy);
      return itemJson(copy);
    }
    if (path === "/api/plan/autofill") return { placed: autofill() };
    if (path === "/api/plan/from-goals") {
      let added = 0;
      for (const g of SEED.goals.filter((g) => g.weekly_hours > 0)) {
        let remaining = g.weekly_hours * 60 - state.items.filter((i) => i.goal_id === g.id).reduce((s, i) => s + i.duration_min, 0);
        while (remaining >= 30) {
          const len = Math.min(90, remaining);
          state.items.push({ id: state.nextId++, title: g.title, kind: "event", category: g.area === "school" ? "revision" : g.area, duration_min: len, start: null, location: "", priority: 3, goal_id: g.id, notes: "", origin: "app", dirty: true });
          remaining -= len;
          added++;
        }
      }
      return { added };
    }
    if (path === "/api/plan/import-todoist") {
      if (state.imported) return { added: 0 };
      state.imported = true;
      SEED.todoist_pool.forEach((t) => state.items.push({ ...t, id: state.nextId++, start: null, origin: "todoist", dirty: true }));
      return { added: SEED.todoist_pool.length };
    }
    if (path === "/api/plan/commit") {
      let events = 0, tasks = 0, removed = 0;
      state.items.forEach((i) => {
        if (i.start) { i.kind === "event" ? events++ : tasks++; i.committed = true; }
        else if (i.committed) { i.committed = false; removed++; }
        i.dirty = false;
      });
      return { events, tasks, removed, errors: [] };
    }
    if (path === "/goals/export") return SEED.export_text;
    throw [404, `Not available in the demo: ${path}`];
  }

  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    const path = url.pathname.replace(/^.*?(\/api\/|\/goals\/export)/, "$1");
    if (!path.startsWith("/api/") && path !== "/goals/export") return realFetch(input, init);
    const method = (init.method || "GET").toUpperCase();
    try {
      const result = route(method, path, init.body ? JSON.parse(init.body) : {});
      save();
      const text = typeof result === "string" ? result : JSON.stringify(result);
      return new Response(text, { status: 200, headers: { "Content-Type": typeof result === "string" ? "text/plain" : "application/json" } });
    } catch (e) {
      const [status, detail] = Array.isArray(e) ? e : [500, String(e)];
      return new Response(JSON.stringify({ detail }), { status, headers: { "Content-Type": "application/json" } });
    }
  };

  // The artifact viewer refuses confirm() dialogs, so the demo skips them.
  window.confirm = () => true;
})();
