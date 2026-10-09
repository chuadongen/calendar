// Page switching for the single-file demo: the real app has one URL per page.
(() => {
  const pages = ["dashboard", "review", "retro", "goals", "plan", "settings"];
  const toast = (msg) => {
    const t = document.getElementById("toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => (t.hidden = true), 3200);
  };

  function show(name) {
    if (!pages.includes(name)) name = "plan";
    document.querySelectorAll("main[data-page]").forEach((m) => (m.hidden = m.dataset.page !== name));
    document.querySelectorAll(".sidebar .nav-link").forEach((a) => {
      a.classList.toggle("active", a.getAttribute("href") === `/${name}`);
    });
    // FullCalendar measures itself on resize; it was laid out while hidden.
    if (name === "plan") requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
    try { history.replaceState(null, "", `#${name}`); } catch (e) {}
    window.scrollTo(0, 0);
  }

  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[href]");
    if (!a) return;
    const href = a.getAttribute("href");
    if (href.startsWith("http") || href.startsWith("#")) return;
    e.preventDefault();
    e.stopPropagation();
    if (href === "/goals/export") {
      fetch("/goals/export").then((r) => r.text()).then((text) => {
        document.getElementById("export-text").textContent = text;
        document.getElementById("export-dialog").showModal();
      });
      return;
    }
    const page = href.split(/[?#]/)[0].replace(/^\//, "");
    if (pages.includes(page)) return show(page);
    if (href.startsWith("?week=")) return toast("Switching weeks is turned off in the demo.");
    toast("That part needs the real server, so it is not in the demo.");
  }, true);

  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (form.method === "dialog" || form.id === "add-form" || form.id === "edit-form") return;
    e.preventDefault();
    toast("Saving goals, retros and settings needs the real server. The Plan page works fully here.");
  }, true);

  // A reset link for the demo data, under the sidebar footer.
  const foot = document.querySelector(".sidebar-foot");
  if (foot) {
    const b = document.createElement("button");
    b.className = "demo-reset";
    b.type = "button";
    b.textContent = "Reset demo data";
    b.onclick = () => window.resetDemo();
    foot.append(document.createElement("br"), b);
  }

  // Re-apply ticks saved from an earlier visit.
  Object.entries(window.demoTaskStates()).forEach(([id, done]) => {
    const row = document.querySelector(`[data-task="${CSS.escape(id)}"]`);
    if (!row) return;
    row.classList.toggle("is-done", done);
    row.querySelector("button.check")?.classList.toggle("done", done);
  });

  const start = (location.hash || "").replace("#", "");
  show(pages.includes(start) ? start : "dashboard");
})();
