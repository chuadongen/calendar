// Planning screen: Todoist-style backlog on the left, Google Calendar-style week on the right.
(() => {
  const { week, dayStart, dayEnd, categories, priorityColors } = window.PLAN;
  const $ = (sel) => document.querySelector(sel);
  const backlogEl = $("#backlog");
  const listEl = $("#backlog-list");
  let items = [];
  let calendar;

  // Helpers

  const api = async (method, url, body) => {
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
    return data;
  };
  const q = (url) => `${url}${url.includes("?") ? "&" : "?"}week=${week}`;

  const toast = (msg, ms = 2600) => {
    const t = $("#toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => (t.hidden = true), ms);
  };

  const fmtDuration = (m) => (m >= 60 ? `${Math.floor(m / 60)}h${m % 60 ? ` ${m % 60}m` : ""}` : `${m}m`);
  const pad = (n) => String(n).padStart(2, "0");
  const toLocalInput = (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  async function guarded(btn, fn) {
    if (btn) btn.disabled = true;
    try {
      await fn();
    } catch (e) {
      toast(e.message, 5000);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // Data

  async function load(refresh = false) {
    const data = await api("GET", q(`/api/plan${refresh ? "?refresh=1" : ""}`));
    items = data.items;
    renderErrors(data.errors);
    renderBacklog();
    renderHours(data.summary);
    renderIssues();
    calendar.removeAllEvents();
    data.busy.forEach((b, i) =>
      calendar.addEvent({
        id: `busy-${i}`,
        title: b.title,
        start: b.start,
        end: b.end,
        allDay: b.allDay,
        editable: false,
        classNames: ["ev-busy", `ev-${b.source}`],
        extendedProps: { busy: true, source: b.source },
      })
    );
    items.filter((i) => i.start).forEach((i) => calendar.addEvent(toEvent(i)));
  }

  function toEvent(item) {
    const classes = [];
    if (!item.committed) classes.push("ev-draft");
    if (item.issues.some((x) => x.level === "clash")) classes.push("ev-clash");
    return {
      id: `item-${item.id}`,
      title: item.title,
      start: item.start,
      end: item.end,
      backgroundColor: item.color,
      borderColor: item.color,
      classNames: classes,
      extendedProps: { item },
    };
  }

  // Rendering

  function renderErrors(errors) {
    $("#errors").innerHTML = errors.length
      ? `<div class="setup-bar">${errors.map(esc).join(" · ")} <a href="/settings">Settings</a></div>`
      : "";
  }

  function renderBacklog() {
    const backlog = items.filter((i) => !i.start);
    $("#backlog-count").textContent = backlog.length ? backlog.length : "";
    listEl.innerHTML = backlog.length
      ? backlog
          .map(
            (i) => `
        <li class="task" data-id="${i.id}" data-title="${esc(i.title)}" data-duration="${i.duration_min}" data-color="${i.color}">
          <span class="check ${i.kind === "event" ? "square" : ""}" style="--prio: ${priorityColors[i.priority]}"></span>
          <div class="task-body">
            <div class="task-title">${esc(i.title)}</div>
            <div class="task-meta">
              <span>${fmtDuration(i.duration_min)}</span>
              ${i.kind === "event" ? "<span>Event</span>" : ""}
              ${i.location ? `<span>📍 ${esc(i.location)}</span>` : ""}
              ${i.goal_title ? `<span class="label">${esc(i.goal_title)}</span>` : ""}
              <span class="project"><span class="dot" style="background:${i.color}"></span> ${esc(categories[i.category]?.label || i.category)}</span>
            </div>
          </div>
        </li>`
          )
          .join("")
      : `<li class="small faint" style="list-style:none; padding: 6px 0">Backlog is empty. Add blocks, import from Todoist, or seed from goals.</li>`;
  }

  function renderHours(summary) {
    const entries = Object.entries(summary.scheduled_hours);
    const total = entries.reduce((sum, [, h]) => sum + h, 0);
    $("#hours").innerHTML = (entries.length ? `<span class="pill" style="color: var(--text)">${Math.round(total * 10) / 10}h planned</span>` : "") + entries
      .map(([k, h]) => `<span class="pill"><span class="dot" style="background:${categories[k]?.color}"></span>${esc(categories[k]?.label || k)} ${h}h</span>`)
      .join("");
  }

  function renderIssues() {
    const withIssues = items.filter((i) => i.start && i.issues.length);
    $("#issues").innerHTML = withIssues.length
      ? `<h3 style="color: var(--danger)">Needs attention (${withIssues.length})</h3><ul>${withIssues
          .map(
            (i) =>
              `<li><a href="#" data-open="${i.id}"><strong>${esc(i.title)}</strong></a> · ${new Date(i.start).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}<br>${[
                ...new Set(i.issues.map((x) => x.message)),
              ]
                .map(esc)
                .join("<br>")}</li>`
          )
          .join("")}</ul>`
      : "";
  }

  // Calendar

  calendar = new FullCalendar.Calendar($("#calendar"), {
    initialView: "timeGridWeek",
    initialDate: week,
    firstDay: 1,
    headerToolbar: false,
    height: "100%",
    nowIndicator: true,
    allDaySlot: true,
    allDayText: "",
    slotDuration: "00:15:00",
    slotLabelInterval: "01:00",
    slotLabelFormat: { hour: "numeric", meridiem: "short" },
    scrollTime: dayStart,
    // Hours outside wake-up to bedtime are shaded as sleep.
    businessHours: { daysOfWeek: [0, 1, 2, 3, 4, 5, 6], startTime: dayStart, endTime: dayEnd },
    snapDuration: "00:15:00",
    editable: true,
    droppable: true,
    eventOverlap: true,
    dragRevertDuration: 0,
    expandRows: false,
    dayHeaderContent: (arg) => ({
      html: `<span class="gc-day"><span class="gc-dow">${arg.date.toLocaleDateString([], { weekday: "short" }).toUpperCase()}</span><span class="gc-date">${arg.date.getDate()}</span></span>`,
    }),
    eventContent: (arg) => {
      const item = arg.event.extendedProps.item;
      const ring = item && item.kind === "task" ? '<span class="ev-ring"></span>' : "";
      const warn = item && item.issues.length ? " ⚠" : "";
      return {
        html: `<div class="ev-title">${ring}<span>${esc(arg.event.title)}${warn}</span></div>${
          arg.timeText && !arg.event.allDay ? `<div class="ev-time">${esc(arg.timeText)}${item?.location ? ` · ${esc(item.location)}` : ""}</div>` : ""
        }`,
      };
    },
    eventDidMount: (arg) => {
      const item = arg.event.extendedProps.item;
      if (item && item.issues.length) arg.el.title = item.issues.map((x) => x.message).join("\n");
      else if (arg.event.extendedProps.busy) arg.el.title = `${arg.event.title} (from ${arg.event.extendedProps.source === "todoist" ? "Todoist" : "Google Calendar"}, locked)`;
    },
    // Backlog item dropped onto the grid.
    drop: (info) =>
      guarded(null, async () => {
        const id = info.draggedEl.dataset.id;
        await api("PATCH", `/api/plan/items/${id}`, { start: info.date.toISOString() });
        await load();
      }),
    eventDrop: (info) => saveMove(info),
    eventResize: (info) => saveMove(info),
    eventDragStart: () => document.addEventListener("pointermove", trackOverBacklog),
    eventDragStop: (info) => {
      document.removeEventListener("pointermove", trackOverBacklog);
      backlogEl.classList.remove("drag-over");
      const item = info.event.extendedProps.item;
      if (item && isOverBacklog(info.jsEvent)) {
        guarded(null, async () => {
          await api("PATCH", `/api/plan/items/${item.id}`, { unschedule: true });
          await load();
        });
      }
    },
    eventClick: (info) => {
      const item = info.event.extendedProps.item;
      if (item) openEditor(item);
    },
    // Select an empty range to create a block there.
    selectable: true,
    selectMirror: true,
    select: (info) => {
      calendar.unselect();
      const minutes = Math.round((info.end - info.start) / 60000);
      if (info.allDay) return;
      openEditor({
        id: null,
        title: "",
        kind: "event",
        category: "school",
        priority: 4,
        start: info.start.toISOString(),
        duration_min: minutes,
        location: "",
        goal_id: null,
        notes: "",
        issues: [],
      });
    },
  });
  calendar.render();

  new FullCalendar.Draggable(listEl, {
    itemSelector: ".task[data-id]",
    eventData: (el) => ({
      title: el.dataset.title,
      duration: { minutes: Number(el.dataset.duration) },
      backgroundColor: el.dataset.color,
      borderColor: el.dataset.color,
      create: false,
    }),
  });

  function isOverBacklog(ev) {
    const r = backlogEl.getBoundingClientRect();
    return ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom;
  }
  function trackOverBacklog(ev) {
    backlogEl.classList.toggle("drag-over", isOverBacklog(ev));
  }

  function saveMove(info) {
    const item = info.event.extendedProps.item;
    if (!item) return info.revert();
    const duration = Math.round((info.event.end - info.event.start) / 60000);
    guarded(null, async () => {
      try {
        await api("PATCH", `/api/plan/items/${item.id}`, { start: info.event.start.toISOString(), duration_min: duration });
      } catch (e) {
        info.revert();
        throw e;
      }
      await load();
    });
  }

  // Editor dialog

  const dialog = $("#edit");
  const form = $("#edit-form");
  let editing = null;

  function openEditor(item) {
    editing = item;
    $("#edit-heading").textContent = item.id ? "Edit block" : "New block";
    form.title.value = item.title;
    form.kind.value = item.kind;
    form.category.value = item.category;
    form.priority.value = item.priority;
    form.start.value = toLocalInput(item.start);
    form.duration_min.value = item.duration_min;
    form.location.value = item.location || "";
    form.goal_id.value = item.goal_id || "";
    form.notes.value = item.notes || "";
    $("#edit-issues").innerHTML = (item.issues || []).map((x) => `<li>${esc(x.message)}</li>`).join("");
    ["#edit-delete", "#edit-unschedule", "#edit-duplicate"].forEach((s) => ($(s).hidden = !item.id));
    dialog.showModal();
    form.title.focus();
  }

  form.addEventListener("submit", (e) => {
    if (e.submitter && e.submitter.value === "cancel") return;
    e.preventDefault();
    const body = {
      title: form.title.value,
      kind: form.kind.value,
      category: form.category.value,
      priority: Number(form.priority.value),
      duration_min: Number(form.duration_min.value),
      location: form.location.value,
      notes: form.notes.value,
      ...(form.goal_id.value ? { goal_id: Number(form.goal_id.value) } : { clear_goal: true }),
      ...(form.start.value ? { start: new Date(form.start.value).toISOString() } : { unschedule: true }),
    };
    guarded(e.submitter, async () => {
      if (editing.id) await api("PATCH", `/api/plan/items/${editing.id}`, body);
      else await api("POST", q("/api/plan/items"), body);
      dialog.close();
      await load();
    });
  });

  $("#edit-delete").addEventListener("click", (e) =>
    guarded(e.currentTarget, async () => {
      if (!confirm(`Delete "${editing.title}"? Anything it created in Google Calendar or Todoist is removed too.`)) return;
      const res = await api("DELETE", `/api/plan/items/${editing.id}`);
      dialog.close();
      if (res.errors.length) toast(res.errors.join(" · "), 5000);
      await load();
    })
  );
  $("#edit-unschedule").addEventListener("click", (e) =>
    guarded(e.currentTarget, async () => {
      await api("PATCH", `/api/plan/items/${editing.id}`, { unschedule: true });
      dialog.close();
      await load();
    })
  );
  $("#edit-duplicate").addEventListener("click", (e) =>
    guarded(e.currentTarget, async () => {
      await api("POST", `/api/plan/items/${editing.id}/duplicate`);
      dialog.close();
      toast("Copy added to the backlog.");
      await load();
    })
  );

  listEl.addEventListener("click", (e) => {
    const li = e.target.closest(".task[data-id]");
    if (li) openEditor(items.find((i) => i.id === Number(li.dataset.id)));
  });
  $("#issues").addEventListener("click", (e) => {
    const a = e.target.closest("[data-open]");
    if (!a) return;
    e.preventDefault();
    openEditor(items.find((i) => i.id === Number(a.dataset.open)));
  });

  // Quick add (Todoist style)

  const addForm = $("#add-form");
  let addKind = "task";
  $("#show-add").addEventListener("click", () => {
    addForm.hidden = false;
    $("#show-add").hidden = true;
    addForm.title.focus();
  });
  $("#cancel-add").addEventListener("click", () => {
    addForm.hidden = true;
    $("#show-add").hidden = false;
  });
  addForm.querySelector(".seg").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    addKind = b.dataset.v;
    addForm.querySelectorAll(".seg button").forEach((x) => x.classList.toggle("on", x === b));
  });
  addForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const body = {
      title: addForm.title.value,
      kind: addKind,
      duration_min: Number(addForm.duration_min.value),
      category: addForm.category.value,
      priority: Number(addForm.priority.value),
      location: addForm.location.value,
      ...(addForm.goal_id.value ? { goal_id: Number(addForm.goal_id.value) } : {}),
    };
    guarded(e.submitter, async () => {
      await api("POST", q("/api/plan/items"), body);
      addForm.title.value = "";
      addForm.title.focus();
      await load();
    });
  });

  // Toolbar

  $("#btn-refresh").addEventListener("click", (e) => guarded(e.currentTarget, () => load(true)));
  $("#btn-goals").addEventListener("click", (e) =>
    guarded(e.currentTarget, async () => {
      const r = await api("POST", q("/api/plan/from-goals"));
      toast(r.added ? `Added ${r.added} block(s) from your goals' weekly hours.` : "Goals' weekly hours are already covered.");
      await load();
    })
  );
  $("#btn-import").addEventListener("click", (e) =>
    guarded(e.currentTarget, async () => {
      const r = await api("POST", q("/api/plan/import-todoist"));
      toast(`Imported ${r.added} task(s) from Todoist.`);
      await load();
    })
  );
  $("#btn-autofill").addEventListener("click", (e) =>
    guarded(e.currentTarget, async () => {
      const r = await api("POST", q("/api/plan/autofill"));
      const left = items.filter((i) => !i.start).length - r.placed;
      toast(`Placed ${r.placed} block(s).${left > 0 ? ` ${left} did not fit.` : ""}`);
      await load();
    })
  );
  $("#btn-commit").addEventListener("click", (e) =>
    guarded(e.currentTarget, async () => {
      const clashes = items.filter((i) => i.issues.some((x) => x.level === "clash")).length;
      if (clashes && !confirm(`${clashes} block(s) still clash. Commit anyway?`)) return;
      const r = await api("POST", q("/api/plan/commit"));
      const parts = [`${r.events} event(s) to Calendar`, `${r.tasks} task(s) to Todoist`];
      if (r.removed) parts.push(`${r.removed} removed`);
      toast(`Committed: ${parts.join(", ")}.${r.errors.length ? ` Problems: ${r.errors.join(" · ")}` : ""}`, r.errors.length ? 8000 : 3500);
      await load(true);
    })
  );

  load().catch((e) => toast(e.message, 5000));
})();
