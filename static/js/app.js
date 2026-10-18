/* 论文写作智能体 前端逻辑（vanilla JS，零构建） */
"use strict";

const $ = (sel) => document.querySelector(sel);
/* ================= 认证（多用户 RBAC） ================= */
const AUTH = {
  token: localStorage.getItem("paper_agent_token") || "",
  user: null,        // {username, role}
  enabled: false,    // 服务端已创建账号（多用户模式）
};
function authHeaders(extra) {
  const h = Object.assign({}, extra || {});
  if (AUTH.token) h.Authorization = "Bearer " + AUTH.token;
  return h;
}
function urlToken() { return AUTH.token ? "&token=" + encodeURIComponent(AUTH.token) : ""; }
function rawUrl(path) { return "/api/file/raw?path=" + encodeURIComponent(path) + urlToken(); }
function showLogin(msg) {
  const ov = $("#authOverlay");
  if (!ov || ov.classList.contains("open")) return;
  ov.classList.add("open");
  if (msg) $("#authMsg").textContent = msg;
  setTimeout(() => $("#loginUser")?.focus(), 50);
}
function hideLogin() { $("#authOverlay")?.classList.remove("open"); }
async function initAuth() {
  try {
    const st = await api("/api/auth/status");
    AUTH.enabled = !!st.enabled;
  } catch (e) { AUTH.enabled = false; }
  if (!AUTH.enabled) { renderAuthChip({ username: "", role: "admin", legacy: true }); return; }
  if (!AUTH.token) { showLogin("请登录后使用"); return; }
  try {
    const me = await api("/api/auth/me");
    AUTH.user = me.user;
    renderAuthChip(AUTH.user);
  } catch (e) {
    AUTH.token = ""; localStorage.removeItem("paper_agent_token");
    showLogin("登录已过期，请重新登录");
  }
}
function renderAuthChip(user) {
  const chip = $("#authChip");
  if (!chip) return;
  if (!user || (!user.username && user.legacy)) {
    chip.innerHTML = '<span class="auth-name">单用户模式</span>'
      + '<span class="act" id="btnEnableAuth" title="创建管理员账号，开启多用户权限隔离">🔐 开启权限</span>';
    chip.querySelector("#btnEnableAuth")?.addEventListener("click", () => {
      $("#authLoginBox").style.display = "none";
      $("#authRegBox").style.display = "";
      showLogin("创建第一个账号（自动成为管理员，并接管现有数据）");
    });
    return;
  }
  const adminBtn = user.role === "admin"
    ? ' <span class="act" id="btnAdminUsers">👥 用户</span>' : "";
  chip.innerHTML = `<span class="auth-name" title="角色：${esc(user.role)}">👤 ${esc(user.username)}`
    + `<span class="auth-role">${user.role === "admin" ? "管理员" : "用户"}</span></span>${adminBtn}`
    + ' <span class="act" id="btnLogout">退出</span>';
  chip.querySelector("#btnLogout")?.addEventListener("click", doLogout);
  chip.querySelector("#btnAdminUsers")?.addEventListener("click", openAdminPanel);
}
async function doLogout() {
  AUTH.token = ""; AUTH.user = null;
  localStorage.removeItem("paper_agent_token");
  location.reload();
}
async function doLogin() {
  const u = $("#loginUser").value.trim(), pw = $("#loginPass").value;
  $("#authMsg").textContent = "";
  try {
    const r = await api("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: u, password: pw }),
    });
    AUTH.token = r.token; AUTH.user = r.user;
    localStorage.setItem("paper_agent_token", r.token);
    hideLogin(); location.reload();
  } catch (e) { $("#authMsg").textContent = e.message; }
}
async function doRegister() {
  const u = $("#regUser").value.trim(), pw = $("#regPass").value;
  $("#authMsg").textContent = "";
  try {
    const r = await api("/api/auth/register", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: u, password: pw }),
    });
    AUTH.token = r.token; AUTH.user = r.user;
    localStorage.setItem("paper_agent_token", r.token);
    toast(r.migrated_legacy ? "已创建管理员账号并接管原有数据" : "账号创建成功", "ok");
    hideLogin(); location.reload();
  } catch (e) { $("#authMsg").textContent = e.message; }
}
/* ---------- 管理员用户面板 ---------- */
async function openAdminPanel() {
  const modal = $("#adminModal");
  if (!modal) return;
  modal.classList.add("open");
  await refreshAdminUsers();
}
async function refreshAdminUsers() {
  const box = $("#adminUserList");
  try {
    const r = await api("/api/admin/users");
    box.innerHTML = (r.users || []).map((u) => `
      <div class="admin-row" data-name="${esc(u.username)}">
        <span class="admin-name">👤 ${esc(u.username)}</span>
        <select class="admin-role" ${u.username === (AUTH.user && AUTH.user.username) ? "disabled" : ""}>
          <option value="user" ${u.role === "user" ? "selected" : ""}>用户</option>
          <option value="admin" ${u.role === "admin" ? "selected" : ""}>管理员</option>
        </select>
        <button class="btn sm" data-act="toggle">${u.disabled ? "启用" : "禁用"}</button>
        <button class="btn sm danger" data-act="pwd">重置密码</button>
        <button class="btn sm danger" data-act="del" ${u.username === (AUTH.user && AUTH.user.username) ? "disabled" : ""}>删除</button>
        ${u.disabled ? '<span class="badge err">已禁用</span>' : ""}
      </div>`).join("");
    box.querySelectorAll(".admin-row").forEach((row) => {
      const name = row.dataset.name;
      row.querySelector("[data-act=toggle]")?.addEventListener("click", async (ev) => {
        const dis = ev.target.textContent.trim() === "禁用";
        try {
          await api(`/api/admin/users/${encodeURIComponent(name)}`, {
            method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ disabled: dis }),
          });
          refreshAdminUsers();
        } catch (e) { toast(e.message, "err"); }
      });
      row.querySelector("[data-act=pwd]")?.addEventListener("click", async () => {
        const pw = await ui.prompt({
          title: "🔑 重置密码", label: `为用户 ${name} 设置新密码（至少 6 位）`, inputType: "password",
          placeholder: "输入新密码", confirmText: "重置",
        });
        if (!pw) return;
        try {
          await api(`/api/admin/users/${encodeURIComponent(name)}`, {
            method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password: pw }),
          });
          toast("密码已重置", "ok");
        } catch (e) { toast(e.message, "err"); }
      });
      row.querySelector("[data-act=del]")?.addEventListener("click", async () => {
        const ok = await ui.confirm({
          title: "删除用户", icon: "🗑",
          html: `确定删除用户 <b>${esc(name)}</b>？<br><span style="font-size:11.5px;color:var(--text-faint)">其数据目录会保留，如需彻底清理请手工删除</span>`,
          danger: true, confirmText: "删除",
        });
        if (!ok) return;
        try {
          await api(`/api/admin/users/${encodeURIComponent(name)}`, { method: "DELETE" });
          refreshAdminUsers();
        } catch (e) { toast(e.message, "err"); }
      });
      row.querySelector(".admin-role")?.addEventListener("change", async (ev) => {
        try {
          await api(`/api/admin/users/${encodeURIComponent(name)}`, {
            method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ role: ev.target.value }),
          });
          toast("角色已更新", "ok");
        } catch (e) { toast(e.message, "err"); refreshAdminUsers(); }
      });
    });
  } catch (e) { box.innerHTML = `<div class="empty-hint">${esc(e.message)}</div>`; }
}
async function adminCreateUser() {
  const u = $("#adminNewUser").value.trim(), pw = $("#adminNewPass").value;
  const role = ($("#adminNewRole") && $("#adminNewRole").value) || "user";
  try {
    await api("/api/admin/users?role=" + role, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: u, password: pw }),
    });
    $("#adminNewUser").value = ""; $("#adminNewPass").value = "";
    toast("用户已创建", "ok");
    refreshAdminUsers();
  } catch (e) { toast(e.message, "err"); }
}

const state = {
  projects: { candidates: [], registered: [] },
  currentProject: null,
  sessions: [],
  activeSession: -1,
  sbView: "main",       // 侧栏会话视图：main=进行中 | archived=归档
  sbQuery: "",          // 侧栏会话搜索词
  hiddenProjects: [],   // 前端隐藏的项目名（随工作区状态持久化，只影响显示）
  outline: null,
  templates: {},
  template: "chinese_journal",  // 论文结构模板 key
  domain: "auto",               // 论文领域（auto=按项目画像自动检测）
  outlineGenerating: false,
  regenerating: new Set(),
  enhancing: new Set(),
  aiChecks: {},
  fulltextPoll: null,
  workspaceRoot: "",
  editingSid: null,     // 正在编辑的节 id（编辑器打开期间阻止重绘/重生成冲掉）
  paperStyle: null,     // 排版设置（null 时用默认值）
  projectUnderstood: false,  // 当前项目已生成《研究项目主体》（准备向导步骤③状态）
  wsHistory: {},        // 每个工作区根目录的快照（会话/项目/配置随根隔离，切换回来可恢复）
};

/* 排版设置默认值：预览与导出共用（键名与后端 export_style/config 查询参数一致） */
const PAPER_STYLE_DEFAULTS = {
  body_font_px: 16, line_height: 2.05, first_indent_em: 2,
  body_align: "justify", table_align: "center", table_width: "full",
  table_font_px: 14, cell_align: "center", fig_width_pct: 100,
};
function paperStyle() {
  state.paperStyle = { ...PAPER_STYLE_DEFAULTS, ...(state.paperStyle || {}) };
  return state.paperStyle;
}

/* ================= 工具 ================= */
function setGenerating(on) {
  document.body.classList.toggle("is-generating", !!on);
}

/* 字数步进器：3万~8万，步长5000 */
function stepWords(delta) {
  const input = $("#totalWords");
  const raw = Number(input.value) || 30000;
  const next = Math.min(80000, Math.max(30000, raw + delta));
  input.value = next;
  input.dispatchEvent(new Event("input"));
}
function toast(msg, type = "") {
  const icons = { ok: "✓", err: "✕", warn: "⚠" };
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.title = "点击关闭";
  if (icons[type]) {
    const ico = document.createElement("span");
    ico.className = "toast-ico";
    ico.textContent = icons[type];
    el.appendChild(ico);
  }
  el.appendChild(document.createTextNode(msg));
  el.addEventListener("click", () => el.remove());
  $("#toastWrap").appendChild(el);
  setTimeout(() => el.remove(), type === "err" ? 6000 : 3800);
}

function md(text) {
  try {
    const html = marked.parse(text || "", { breaks: true });
    return DOMPurify.sanitize(html);
  } catch (e) { return DOMPurify.sanitize(text || ""); }
}

async function api(path, options = {}) {
  options.headers = authHeaders(options.headers);
  const resp = await fetch(path, options);
  if (resp.status === 401) { showLogin("请先登录"); throw new Error("未登录或令牌失效"); }
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail || detail; } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  return resp.json();
}

/* SSE 流式读取：onEvent(obj) 处理每个 data: JSON；结束返回。
   断流看门狗：120 秒无任何数据视为连接死亡，主动 cancel 让调用方的 catch 兜底。 */
async function streamSSE(path, body, onEvent) {
  const resp = await fetch(path, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (resp.status === 401) { showLogin("请先登录"); throw new Error("未登录或令牌失效"); }
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail || detail; } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lastBeat = Date.now();
  const watchdog = setInterval(() => {
    if (Date.now() - lastBeat > 120000) {
      try { reader.cancel(new Error("SSE 流超时（120 秒无数据）")); } catch (e) { /* ignore */ }
    }
  }, 15000);
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      lastBeat = Date.now();
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop();
      for (const part of parts) {
        const line = part.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        const payload = line.slice(5).trim();
        if (payload === "[DONE]") return;
        let ev;
        try { ev = JSON.parse(payload); } catch (e) { continue; } // 不完整块跳过
        onEvent(ev);
      }
    }
  } finally {
    clearInterval(watchdog);
  }
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function projectRootPath() {
  const r = (state.projects.registered || []).find((x) => x.name === state.currentProject)
    || (state.projects.candidates || []).find((x) => x.name === state.currentProject);
  return r ? r.path.replace(/[\\/]+$/, "") : "";
}

function resolveImgSrc(src) {
  if (!src) return src;
  let s = src;
  try { s = decodeURIComponent(src); } catch (e) { /* 保留原样 */ }
  if (/^(https?:|data:)/i.test(s)) return src;
  if (s.startsWith("/api/")) return s;
  if (/^[A-Za-z]:[\\/]/.test(s)) return rawUrl(s);
  const root = projectRootPath();
  if (root) return rawUrl(root + "/" + s);
  return src;
}

function renderFormulas(el) {
  if (!el || typeof renderMathInElement !== "function") return;
  try {
    renderMathInElement(el, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "\\(", right: "\\)", display: false },
      ],
      throwOnError: false,
    });
  } catch (e) { /* 公式渲染失败不影响正文 */ }
}

function fixArticleImages(container) {
  if (!container) return;
  container.querySelectorAll("img").forEach((img) => {
    const fixed = resolveImgSrc(img.getAttribute("src") || "");
    if (fixed && img.src !== location.origin + fixed && img.getAttribute("data-fixed") !== fixed) {
      img.src = fixed;
      img.setAttribute("data-fixed", fixed);
    }
    img.loading = "lazy";
    if (img.parentElement && img.parentElement.classList.contains("paper-fig")) return;
    const fig = document.createElement("figure");
    fig.className = "paper-fig";
    const cap = document.createElement("figcaption");
    cap.className = "fig-cap";
    cap.textContent = img.alt || "";
    img.parentNode.insertBefore(fig, img);
    fig.appendChild(img);
    if (img.alt) fig.appendChild(cap);
    // 论文区图片：悬停换图工具
    const secWrap = fig.closest("[data-paper-sid]");
    if (secWrap && img.dataset.path) {
      const tools = document.createElement("span");
      tools.className = "fig-tools";
      tools.innerHTML = '<button data-figup title="上传新图替换">⬆ 上传更换</button>' +
        '<button data-figpick title="从项目已有图片中选择">🖼 项目选图</button>';
      fig.classList.add("has-tools");
      fig.appendChild(tools);
      tools.querySelector("[data-figup]").addEventListener("click", (ev) => {
        ev.stopPropagation();
        openFigureUpload(secWrap.dataset.paperSid, img.dataset.path);
      });
      tools.querySelector("[data-figpick]").addEventListener("click", (ev) => {
        ev.stopPropagation();
        openFigurePicker(secWrap.dataset.paperSid, img.dataset.path);
      });
    }
  });
}

function mdWithFigs(text) {
  const figs = [];
  const maths = [];
  let masked = String(text ?? "").replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (m, alt, src) => {
    figs.push({ alt, src });
    return "FIGPLACEHOLDER" + (figs.length - 1) + "ENDPLACEHOLDER";
  });
  // 数学式掩蔽：防止 markdown 吃掉 LaTeX 的下划线/星号
  masked = masked.replace(/\$\$([\s\S]+?)\$\$/g, (m, tex) => {
    maths.push({ tex: tex.trim(), display: true });
    return "MATHPLACEHOLDER" + (maths.length - 1) + "ENDMATH";
  });
  masked = masked.replace(/\\\(([\s\S]+?)\\\)/g, (m, tex) => {
    maths.push({ tex: tex.trim(), display: false });
    return "MATHPLACEHOLDER" + (maths.length - 1) + "ENDMATH";
  });
  let html = md(masked);
  html = html.replace(/FIGPLACEHOLDER(\d+)ENDPLACEHOLDER/g, (m, i) => {
    const f = figs[Number(i)];
    if (!f) return "";
    return '<img src="' + resolveImgSrc(f.src) + '" alt="' + esc(f.alt) + '" data-path="' + esc(f.src) + '">';
  });
  html = html.replace(/MATHPLACEHOLDER(\d+)ENDMATH/g, (m, i) => {
    const mt = maths[Number(i)];
    if (!mt) return "";
    try {
      return katex.renderToString(mt.tex, { displayMode: mt.display, throwOnError: false });
    } catch (e) { return esc("$$" + mt.tex + "$$"); }
  });
  return html;
}

function renderTokens(text) {
  return String(text ?? "")
    .replace(/\[\[文件夹:([^\]]+)\]\]/g, '<span class="file-token dir">📁 $1</span>')
    .replace(/\[\[文件:([^\]]+)\]\]/g, '<span class="file-token">📎 $1</span>');
}

/* ================= 可拖拽分隔条 ================= */
const PANE_LIMITS = {
  left:  { min: 200, max: 460, def: 264 },
  preview: { min: 320, max: 1000, def: 560 },
  chat:  { min: 280, max: 620, def: 385 },
  right: { min: 300, max: 620, def: 400 },
};
function paneEl(name) {
  if (name === "left") return document.querySelector(".pane-left");
  if (name === "chat") return document.querySelector("#chatPanel");
  if (name === "preview") return document.querySelector("#previewPane");
  return document.querySelector(".pane-right");
}
function paneWidth(name) {
  const el = paneEl(name);
  return el ? Math.round(el.getBoundingClientRect().width) : PANE_LIMITS[name].def;
}
function applyPaneWidth(name, w) {
  const L = PANE_LIMITS[name];
  const clamped = Math.round(Math.min(L.max, Math.max(L.min, w)));
  const el = paneEl(name);
  if (el) { el.style.width = clamped + "px"; el.style.flex = "0 0 auto"; }
  return clamped;
}
function initSplitters() {
  document.querySelectorAll(".vsplit").forEach((sp) => {
    const name = sp.dataset.pane;
    sp.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      sp.setPointerCapture(e.pointerId);
      sp.classList.add("active");
      document.body.classList.add("resizing");
      const rightW = () => paneWidth("right");
      const move = (ev) => {
        let w;
        if (name === "left") w = ev.clientX;
        else if (name === "preview") {
          const midLeft = document.querySelector(".pane-mid").getBoundingClientRect().left;
          w = ev.clientX - midLeft;
        }
        else if (name === "chat") w = window.innerWidth - ev.clientX - rightW();
        else w = window.innerWidth - ev.clientX;
        applyPaneWidth(name, w);
      };
      const up = () => {
        sp.removeEventListener("pointermove", move);
        sp.removeEventListener("pointerup", up);
        sp.removeEventListener("pointercancel", up);
        sp.classList.remove("active");
        document.body.classList.remove("resizing");
        scheduleSave();
      };
      sp.addEventListener("pointermove", move);
      sp.addEventListener("pointerup", up);
      sp.addEventListener("pointercancel", up);
    });
    sp.addEventListener("dblclick", () => {
      applyPaneWidth(name, PANE_LIMITS[name].def);
      scheduleSave();
    });
  });
}

/* ================= 主题切换 ================= */
const THEMES = ["dark", "light", "paper"];  // v19：codex/qoder 并入唯一深色主题
const DARK_THEMES = ["dark"];
const THEME_LEGACY = { codex: "dark", qoder: "dark", mint: "light" };
function applyTheme(name) {
  name = THEME_LEGACY[name] || name;   // 旧主题值平滑迁移
  const theme = THEMES.includes(name) ? name : "dark";
  document.body.dataset.theme = theme;
  const isDark = DARK_THEMES.includes(theme);
  document.documentElement.style.colorScheme = isDark ? "dark" : "light";
  document.querySelectorAll(".theme-dot").forEach((d) =>
    d.classList.toggle("active", d.dataset.theme === theme));
  // 代码高亮主题随动：深色 atom-one-dark / 浅色系 github
  const hlDark = document.querySelector("#hljsDark");
  const hlLight = document.querySelector("#hljsLight");
  if (hlDark && hlLight) {
    hlDark.disabled = !isDark;
    hlLight.disabled = isDark;
  }
}

/* ================= 论文区划词 → 添加到会话 ================= */
const selPop = () => document.querySelector("#selPop");
let paperSelText = "";

function hideSelPop() {
  const el = selPop();
  if (el) el.classList.remove("show");
}

function tryShowSelPop() {
  const el = selPop();
  const view = document.querySelector("#paperView");
  if (!el || !view) return;
  const sel = document.getSelection();
  const text = sel ? sel.toString().trim() : "";
  if (!text || text.length < 2 || sel.rangeCount === 0) { hideSelPop(); return; }
  const node = sel.anchorNode;
  const host = node && (node.nodeType === 1 ? node : node.parentElement);
  if (!host || !view.contains(host)) { hideSelPop(); return; }
  if (host.closest && host.closest(".sec-editor")) { hideSelPop(); return; } // 编辑器内选区不弹
  const rect = sel.getRangeAt(0).getBoundingClientRect();
  paperSelText = text;
  el.style.left = Math.min(Math.max(rect.left + rect.width / 2, 90), window.innerWidth - 90) + "px";
  el.style.top = Math.max(rect.top - 36, 54) + "px";
  el.classList.remove("show");
  void el.offsetWidth;
  el.classList.add("show");
}

function bindSelPop() {
  const view = document.querySelector("#paperView");
  if (!view) return;
  view.addEventListener("mouseup", () => setTimeout(tryShowSelPop, 10));
  view.addEventListener("touchend", () => setTimeout(tryShowSelPop, 10));
  view.addEventListener("scroll", hideSelPop, true);
  document.addEventListener("mousedown", (e) => {
    const el = selPop();
    if (el && !el.contains(e.target)) hideSelPop();
  });
  window.addEventListener("resize", hideSelPop);
  selPop().addEventListener("mousedown", (e) => e.preventDefault()); // 防止点击时先清空选区
  selPop().addEventListener("click", () => {
    const sess = activeSession();
    const text = paperSelText;
    hideSelPop();
    if (!sess || !text) return;
    sess.pendingSelections = sess.pendingSelections || [];
    sess.pendingSelections.push(text);
    renderRefBar();
    const panel = document.querySelector("#chatInput");
    panel && panel.focus();
    toast("选段已挂到聊天框上方，随下一条消息发送给模型", "ok");
    document.getSelection().removeAllRanges();
  });
}

/* ================= 主题化下拉（v13） =================
   原生 <select> 弹层由操作系统绘制、不随主题变色（浅色/纸张主题下白底蓝高亮，与界面割裂）。
   方案：隐藏原生控件，自绘触发器 + 弹层；选项实时读取原生 select（重灌选项自动跟随），
   选中后回写 value 并派发 change，既有业务逻辑零改动。 */
let _tsOpen = null; // 当前打开的下拉状态 { sel, trig, menu, opts, active, setActive }

function _tsSync(trig, sel) {
  const opt = sel.selectedOptions && sel.selectedOptions[0];
  const label = trig.querySelector(".tselect-label");
  if (label) label.textContent = opt ? opt.textContent.trim() : "";
  trig.disabled = sel.disabled;
}

function _tsBuild(sel) {
  if (sel.dataset.tsReady) return;
  sel.dataset.tsReady = "1";
  sel.classList.add("tselect-native");
  const trig = document.createElement("button");
  trig.type = "button";
  trig.className = "tselect";
  if (sel.title) trig.title = sel.title;
  trig.innerHTML = '<span class="tselect-label"></span>';
  sel.insertAdjacentElement("afterend", trig);
  _tsSync(trig, sel);

  // 选项重建 / 选中态变化时同步触发器显示
  new MutationObserver(() => _tsSync(trig, sel))
    .observe(sel, { childList: true, subtree: true, attributes: true, attributeFilter: ["selected", "disabled"] });
  // 程序直接赋值 sel.value 不触发任何事件，低频轮询兜底（仅比对后才写 DOM）
  const drift = setInterval(() => {
    if (!trig.isConnected) { clearInterval(drift); return; }
    _tsSync(trig, sel);
  }, 1200);

  sel.addEventListener("change", () => _tsSync(trig, sel));
  trig.addEventListener("click", () =>
    (_tsOpen && _tsOpen.trig === trig) ? _tsClose() : _tsOpenMenu(sel, trig));
  trig.addEventListener("keydown", (e) => _tsKey(e, sel, trig));
}

function _tsOpenMenu(sel, trig) {
  _tsClose();
  const opts = [...sel.options];
  const menu = document.createElement("div");
  menu.className = "tselect-menu";
  menu.setAttribute("role", "listbox");
  const st = { sel, trig, menu, opts, active: Math.max(0, opts.findIndex((o) => o.selected)) };
  st.setActive = (i) => {
    if (!opts.length) return;
    st.active = ((i % opts.length) + opts.length) % opts.length;
    [...menu.children].forEach((c, j) => c.classList.toggle("active", j === st.active));
    const el = menu.children[st.active];
    el && el.scrollIntoView({ block: "nearest" });
  };
  st.render = () => {
    menu.innerHTML = "";
    if (!opts.length) { menu.innerHTML = '<div class="tselect-empty">暂无选项</div>'; return; }
    opts.forEach((o, i) => {
      const it = document.createElement("div");
      it.className = "tselect-opt" + (o.selected ? " sel" : "") + (i === st.active ? " active" : "");
      it.setAttribute("role", "option");
      it.title = o.textContent.trim();
      const txt = document.createElement("span");
      txt.className = "txt";
      txt.textContent = o.textContent.trim();
      const ck = document.createElement("span");
      ck.className = "ck";
      ck.textContent = "✓";
      it.append(txt, ck);
      it.addEventListener("mouseenter", () => st.setActive(i));
      it.addEventListener("click", () => _tsPick(st, i));
      menu.appendChild(it);
    });
    const el = menu.children[st.active];
    el && el.scrollIntoView({ block: "nearest" });
  };
  st.render();
  document.body.appendChild(menu);
  const r = trig.getBoundingClientRect();
  menu.style.minWidth = Math.max(r.width, 160) + "px";
  const mh = menu.offsetHeight, mw = menu.offsetWidth;
  let top = r.bottom + 6, up = false;
  if (top + mh > innerHeight - 8 && r.top - mh - 6 > 8) { top = r.top - mh - 6; up = true; }
  menu.classList.toggle("up", up);
  menu.style.top = Math.max(8, Math.min(top, innerHeight - mh - 8)) + "px";
  menu.style.left = Math.max(8, Math.min(r.left, innerWidth - mw - 8)) + "px";
  trig.classList.add("open");
  _tsOpen = st;
}

function _tsPick(st, i) {
  const o = st.opts[i];
  if (!o || o.disabled) return;
  st.sel.value = o.value;
  st.sel.dispatchEvent(new Event("change", { bubbles: true }));
  _tsSync(st.trig, st.sel);
  _tsClose();
}

function _tsClose() {
  if (!_tsOpen) return;
  _tsOpen.trig.classList.remove("open");
  _tsOpen.menu.remove();
  _tsOpen = null;
}

function _tsKey(e, sel, trig) {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (!_tsOpen || _tsOpen.trig !== trig) _tsOpenMenu(sel, trig);
    _tsOpen.setActive(_tsOpen.active + (e.key === "ArrowDown" ? 1 : -1));
  } else if ((e.key === "Enter" || e.key === " ") && _tsOpen && _tsOpen.trig === trig) {
    e.preventDefault();
    _tsPick(_tsOpen, _tsOpen.active);
  } else if (e.key === "Escape" || e.key === "Tab") {
    _tsClose();
  }
}

function enhanceAllSelects(root) {
  (root || document).querySelectorAll("select").forEach((s) => {
    if (s.multiple || s.size > 1) return;
    _tsBuild(s);
  });
}

// 全局关闭：点弹层外 / 视口滚动缩放（弹层内部滚动除外）
document.addEventListener("pointerdown", (e) => {
  if (!_tsOpen) return;
  if (_tsOpen.menu.contains(e.target) || _tsOpen.trig.contains(e.target)) return;
  _tsClose();
}, true);
window.addEventListener("resize", _tsClose, { passive: true });
window.addEventListener("scroll", (e) => {
  if (_tsOpen && e.target !== _tsOpen.menu) _tsClose();
}, { capture: true, passive: true });

/* ================= 左栏收展（墨记 logo 开关，v13） ================= */
function setLeftCollapsed(collapsed) {
  // v18：布局改为侧栏式，收展即折叠 .sidebar（保留旧 localStorage 键）
  const shell = $("#appShell");
  if (shell) shell.classList.toggle("sb-collapsed", !!collapsed);
  document.body.classList.toggle("left-collapsed", collapsed);
  try { localStorage.setItem("moji.leftCollapsed", collapsed ? "1" : "0"); } catch (_) {}
}
function bindLeftToggle() {
  const btn = $("#btnToggleLeft");
  btn && btn.addEventListener("click", () =>
    setLeftCollapsed(!document.body.classList.contains("left-collapsed")));
}
function restoreLeftCollapsed() {
  // 在工作区 pane 宽度还原之后调用，才能记住用户的真实宽度
  try {
    if (localStorage.getItem("moji.leftCollapsed") === "1") setLeftCollapsed(true);
  } catch (_) {}
}

/* ================= 设置弹窗（模型配置等，v16）：主题旁 ⚙ 入口 ================= */
const SET = { modal: null };

function ensureSettingsModal() {
  // v19：整页设置存在时直接接管（页面含同构 data-set 字段与 settingsSave，fill/save 复用）
  const page = document.getElementById("settingsPage");
  if (page) {
    if (SET.modal === page) return;
    SET.modal = page;
    page.querySelector("#settingsSave")?.addEventListener("click", saveSettings);
    page.querySelector("#settingsCancel")?.addEventListener("click", () =>
      page.classList.remove("open"));
    // 朱雀独立分区的保存/取消（同一保存流：saveSettings 汇总全部 data-set 字段）
    page.querySelector("#zhuqueSave")?.addEventListener("click", saveSettings);
    page.querySelector("#zhuqueCancel")?.addEventListener("click", () =>
      page.classList.remove("open"));
    page.querySelector("#spBack")?.addEventListener("click", () =>
      page.classList.remove("open"));
    page.querySelectorAll(".sp-nav-item").forEach((item) => {
      item.addEventListener("click", () => {
        page.querySelectorAll(".sp-nav-item").forEach((i) =>
          i.classList.toggle("active", i === item));
        const key = item.dataset.sp;
        page.querySelectorAll(".sp-section").forEach((s) =>
          s.classList.toggle("active", s.dataset.sp === key));
      });
    });
    bindPagePrefs();
    enhanceAllSelects(page);
    return;
  }
  if (SET.modal) return;
  const modal = document.createElement("div");
  modal.id = "settingsModal";
  modal.className = "uw-overlay";
  modal.innerHTML =
    '<div class="uw-dialog" style="width:min(580px,94vw);height:auto;min-height:0;">'
    + '<div class="uw-head"><div class="uw-head-main">'
    + '<div class="uw-head-title">⚙ 设置</div>'
    + '<div class="uw-head-sub">模型选择与生成参数 · 保存即写入 config.yaml 并热生效（无需重启）</div></div>'
    + '<button class="btn-mini" id="settingsClose">✕</button></div>'
    + '<div class="uw-content">'
    + '<div class="set-group-title">模型配置</div>'
    + '<div class="set-row"><label>主写作模型</label><select data-set="llm"></select></div>'
    + '<div class="set-hint">大纲 / 正文 / 自查等重任务（按模型名自动路由：智谱 / DeepSeek）</div>'
    + '<div class="set-row"><label>项目理解模型</label><select data-set="analyst_llm"></select></div>'
    + '<div class="set-hint">「🧠 理解项目」Agent 的扫描分析与《研究项目主体》合成</div>'
    + '<div class="set-row"><label>视觉模型</label><select data-set="vision_model"></select></div>'
    + '<div class="set-hint">图片分析与多模态文献解析（glm-5.3-flash 或 DeepSeek 多模态）</div>'
    + '<div class="set-row"><label>本地小模型</label><input type="text" data-set="small_llm" placeholder="Ollama 模型名"></div>'
    + '<div class="set-hint">Ollama 本地模型：意图路由 / 查询改写（保存后需重启服务生效）</div>'
    + '<div class="set-row"><label>嵌入模型</label><input type="text" data-set="embedding" disabled></div>'
    + '<div class="set-hint">只读：切换嵌入需重建知识库，如需修改请在 config.yaml 中手动变更</div>'
    + '<div class="set-group-title">DeepSeek 接口</div>'
    + '<div class="set-row"><label>DeepSeek API Key</label><input type="password" data-set="deepseek_key" placeholder="sk-…（未配置）" autocomplete="off"></div>'
    + '<div class="set-hint" id="deepseekKeyState">未配置 · 选择 deepseek-4.1-flash 前请先填写（写入 .env，保存即生效）</div>'
    + '<div class="set-row"><label>接口地址</label><input type="text" data-set="deepseek_base_url" placeholder="https://api.deepseek.com"></div>'
    + '<div class="set-hint">OpenAI 兼容端点；使用第三方网关时按网关文档修改</div>'
    + '<div class="set-row"><label>真实模型 ID</label><input type="text" data-set="deepseek_model_id" placeholder="deepseek-flash"></div>'
    + '<div class="set-hint">发给服务端的 model 名（界面选项为 deepseek-4.1-flash）</div>'
    + '<div class="set-group-title">生成参数</div>'
    + '<div class="set-row"><label>温度</label><input type="number" data-set="temperature" min="0" max="1" step="0.05"></div>'
    + '<div class="set-hint">学术写作建议 0.3 ~ 0.5（低温保稳定）</div>'
    + '<div class="set-row"><label>最大 tokens</label><input type="number" data-set="max_tokens" min="4096" max="65536" step="1024"></div>'
    + '<div class="set-hint">单次响应上限；大纲 / 长文生成建议 ≥ 8192</div>'
    + '<div class="set-group-title">外部服务</div>'
    + '<div class="set-row"><label>朱雀 API Key</label><input type="password" data-set="zhuque_key" id="settingsZhuqueKey" placeholder="EdgeOne 控制台创建（留空=不修改）" autocomplete="off"></div>'
    + '<div class="set-hint" id="settingsZhuqueHint">腾讯朱雀 AI 检测（AI 检测中心使用）· 月免费 50 万 token · 保存后立即生效</div>'
    + '</div>'
    + '<div class="uw-foot">'
    + '<span class="uw-foot-note">主模型 / 温度等保存后立即应用于后续生成任务</span>'
    + '<button class="btn-mini" id="settingsCancel">取消</button>'
    + '<button class="btn-mini primary" id="settingsSave">保存并生效</button>'
    + '</div></div>';
  document.body.appendChild(modal);
  modal.querySelector("#settingsClose").addEventListener("click", () => modal.classList.remove("open"));
  modal.querySelector("#settingsCancel").addEventListener("click", () => modal.classList.remove("open"));
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.classList.remove("open"); });
  modal.querySelector("#settingsSave").addEventListener("click", saveSettings);
  enhanceAllSelects(modal);
  SET.modal = modal;
}

/* ---- v19 外观/代码偏好（localStorage 即时生效） ---- */
function loadUiPrefs() {
  try { return JSON.parse(localStorage.getItem("moji.uiPrefs") || "{}"); }
  catch (e) { return {}; }
}
function applyUiPrefs() {
  const p = Object.assign({ uiFont: 14, codeFont: 12.5, lineNums: true, sendKey: "ctrl-enter" }, loadUiPrefs());
  document.body.style.zoom = p.uiFont / 14;                 // 界面字号：整体缩放
  document.documentElement.style.setProperty("--pv-code-size", p.codeFont + "px");
  const pv = $("#previewPane"), sp = $("#spPreviewCode");
  [pv, sp].forEach((el) => el && el.classList.toggle("no-gutter", !p.lineNums));
  // 发送键提示随设置联动
  const hint = $("#composerHint");
  if (hint) hint.textContent = p.sendKey === "enter"
    ? "Enter 发送 · Shift+Enter 换行" : "Enter 换行 · Ctrl+Enter 发送";
  // 回填控件
  const ui = $("#spUiFont"), cf = $("#spCodeFont"), ln = $("#spLineNums"), th = $("#spTheme"), sk = $("#spSendKey");
  if (ui) ui.value = p.uiFont;
  if (cf) cf.value = p.codeFont;
  if (ln) ln.checked = !!p.lineNums;
  if (th) th.value = document.body.dataset.theme || "dark";
  if (sk) sk.value = p.sendKey;
}
function saveUiPrefs(patchObj) {
  const p = Object.assign(loadUiPrefs(), patchObj);
  localStorage.setItem("moji.uiPrefs", JSON.stringify(p));
  applyUiPrefs();
}
function bindPagePrefs() {
  $("#spTheme")?.addEventListener("change", (e) => {
    applyTheme(e.target.value);
    scheduleSave();  // 主题同时进工作区状态
  });
  $("#spUiFont")?.addEventListener("change", (e) =>
    saveUiPrefs({ uiFont: Math.min(24, Math.max(12, Number(e.target.value) || 14)) }));
  $("#spCodeFont")?.addEventListener("change", (e) =>
    saveUiPrefs({ codeFont: Math.min(16, Math.max(10, Number(e.target.value) || 12.5)) }));
  $("#spLineNums")?.addEventListener("change", (e) =>
    saveUiPrefs({ lineNums: e.target.checked }));
  $("#spSendKey")?.addEventListener("change", (e) =>
    saveUiPrefs({ sendKey: e.target.value }));
  $("#spChangeRoot")?.addEventListener("click", () => {
    SET.modal.classList.remove("open");
    setWorkspaceRoot();
  });
}

async function openSettings() {
  ensureSettingsModal();
  SET.modal.classList.add("open");
  const page = SET.modal;
  if (page.id === "settingsPage") {
    // 常规分区：工作区 / 权限状态
    const ws = $("#spWorkspace");
    if (ws) ws.textContent = state.workspaceRoot || "未设置（首次使用请先设置）";
    try {
      const st = await api("/api/auth/status");
      const chip = $("#spAuthState");
      if (chip) {
        chip.textContent = st.enabled ? `多用户已启用（${st.users} 个账号）` : "单用户模式";
      }
    } catch (e) { /* 忽略 */ }
  }
  try {
    const d = await api("/api/settings");
    fillSettingsForm(d);
  } catch (e) {
    toast(`读取设置失败: ${e.message}`, "err");
  }
}

function fillSettingsForm(d) {
  const q = (k) => SET.modal.querySelector(`[data-set="${k}"]`);
  const modelLabel = (m) => !m ? "" : (m.startsWith("deepseek") ? `${m}（DeepSeek）` : `${m}（智谱）`);
  const opt = (sel, list, cur) => {
    sel.innerHTML = list.map((m) => `<option value="${esc(m)}">${esc(modelLabel(m))}</option>`).join("");
    sel.value = cur;
  };
  opt(q("llm"), d.options.llm, d.models.llm);
  opt(q("analyst_llm"), d.options.analyst_llm, d.models.analyst_llm);
  opt(q("vision_model"), d.options.vision, d.vision.model);
  q("small_llm").value = d.models.small_llm;
  // DeepSeek 接口：key 只显示状态（留空=不修改），端点/真实模型 ID 可编辑
  const ds = d.deepseek || {};
  const dsKeyEl = q("deepseek_key");
  if (dsKeyEl) {
    dsKeyEl.value = "";
    dsKeyEl.placeholder = ds.configured
      ? `已配置（${ds.masked}）· 留空保持不变` : "sk-…（未配置）";
  }
  const dsBase = q("deepseek_base_url");
  if (dsBase) dsBase.value = ds.base_url || "";
  const dsModel = q("deepseek_model_id");
  if (dsModel) dsModel.value = ds.model_id || "";
  const dsChip = SET.modal.querySelector("#deepseekKeyState");
  if (dsChip) {
    dsChip.textContent = ds.configured
      ? `✓ 已配置（${ds.masked}）· 端点 ${ds.base_url} · 模型 ID ${ds.model_id}`
      : "未配置 · 选择 deepseek-4.1-flash 前请先填写（写入 .env，保存即生效）";
    dsChip.style.color = ds.configured ? "var(--green)" : "var(--orange)";
  }
  const zk = AIC.modal ? null : null; // no-op
  const zqState = d.zhuque || {};
  const zkInput = SET.modal.querySelector("#settingsZhuqueKey");
  if (zkInput) {
    zkInput.value = "";
    zkInput.placeholder = zqState.configured
      ? `已配置（${zqState.masked}）· 留空保持不变`
      : "粘贴 EdgeOne API Key（未配置）";
  }
  // 朱雀分区状态徽标
  const zkChip = SET.modal.querySelector("#zhuqueStateChip");
  if (zkChip) {
    zkChip.textContent = zqState.configured
      ? `✓ 已配置（${zqState.masked}）` : "✗ 未配置（本地引擎可用）";
    zkChip.style.color = zqState.configured ? "var(--green)" : "var(--orange)";
    zkChip.style.borderColor = zqState.configured
      ? "color-mix(in srgb, var(--green) 40%, transparent)"
      : "color-mix(in srgb, var(--orange) 40%, transparent)";
  }
  q("embedding").value = d.models.embedding;
  q("temperature").value = d.models.temperature;
  q("max_tokens").value = d.models.max_tokens;
}

async function saveSettings() {
  const g = (k) => SET.modal.querySelector(`[data-set="${k}"]`);
  const payload = {
    models: {
      llm: g("llm").value,
      analyst_llm: g("analyst_llm").value,
      small_llm: g("small_llm").value.trim(),
      temperature: parseFloat(g("temperature").value) || 0.4,
      max_tokens: parseInt(g("max_tokens").value) || 16384,
    },
    vision: { model: g("vision_model").value },
  };
  const zkVal = (g("zhuque_key").value || "").trim();
  if (zkVal) payload.zhuque_key = zkVal;
  // DeepSeek 接口：只提交用户实际填写/变更的字段
  const dsKeyEl = g("deepseek_key"), dsBaseEl = g("deepseek_base_url"),
        dsModelEl = g("deepseek_model_id");
  const ds = {};
  if (dsKeyEl && dsKeyEl.value.trim()) ds.api_key = dsKeyEl.value.trim();
  if (dsBaseEl && dsBaseEl.value.trim()) ds.base_url = dsBaseEl.value.trim();
  if (dsModelEl && dsModelEl.value.trim()) ds.model_id = dsModelEl.value.trim();
  if (Object.keys(ds).length) payload.deepseek = ds;
  const btn = SET.modal.querySelector("#settingsSave");
  btn.disabled = true;
  btn.textContent = "保存中…";
  try {
    const res = await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.warnings && res.warnings.length) {
      toast(`设置已保存：${res.warnings.join("；")}`, "warn");
    } else {
      toast("设置已保存并热生效", "ok");
    }
    SET.modal.classList.remove("open");
    refreshSystemInfo();
  } catch (e) {
    toast(`保存失败: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "保存并生效";
  }
}

/* ================= AI 检测中心（v22）：整篇/逐节/上传文档送检 + 9B改写复检 + 报告导出 ================= */
const AIC = { modal: null, running: false, lastReport: null,
              doc: null, humanize: null, docReport: null, recheck: null, rewriting: false,
              verify: null, reportMarks: [], lastChanged: [] };

function ensureAiCenterModal() {
  if (AIC.modal) return;
  const modal = document.createElement("div");
  modal.id = "aiCenterModal";
  modal.className = "uw-overlay";
  modal.innerHTML =
    '<div class="uw-dialog" style="width:min(780px,94vw);height:auto;min-height:0;max-height:92vh;">'
    + '<div class="uw-head"><div class="uw-head-main">'
    + '<div class="uw-head-title">🛡 AI 检测中心</div>'
    + '<div class="uw-head-sub">朱雀官方 API（腾讯·月免费50万token）+ 本地 BERT 双引擎 · 上传文档可 9B 改写复检</div></div>'
    + '<button class="btn-mini" id="aicClose">✕</button></div>'
    + '<div class="uw-content">'
    + '<div class="set-group-title">检测范围</div>'
    + '<div class="aic-modes">'
    + '<label class="aic-mode"><input type="radio" name="aicMode" value="full" checked>'
    + '<span class="aic-mode-name">📄 整篇检测</span><span class="aic-mode-sub">全文一次送检（朱雀合并口径=网页版算法），约 20~60 秒</span></label>'
    + '<label class="aic-mode"><input type="radio" name="aicMode" value="sections">'
    + '<span class="aic-mode-name">📑 逐节检测</span><span class="aic-mode-sub">每节独立三引擎体检，定位薄弱章节，约 1~3 分钟</span></label>'
    + '<label class="aic-mode"><input type="radio" name="aicMode" value="upload">'
    + '<span class="aic-mode-name">📁 上传文档检测</span><span class="aic-mode-sub">word/txt 上送三引擎查重，选中段落用微调 9B 模型改写并自动复检</span></label>'
    + '<label class="aic-mode"><input type="radio" name="aicMode" value="report">'
    + '<span class="aic-mode-name">📥 导入检测文档</span><span class="aic-mode-sub">导入外部 AI 检测报告/已检测论文：先核验是否为当前任务论文，再提取 AI 严重段落用 9B 改写并回写</span></label>'
    + '</div>'
    + '<div class="aic-upload-box" id="aicUploadBox" style="display:none;">'
    + '<input type="file" id="aicFileInput" accept=".docx,.txt,.md,.pdf" style="display:none;">'
    + '<div class="aic-up-empty" id="aicUpEmpty">'
    + '<div class="aic-up-ico">📄</div>'
    + '<div class="aic-up-title" id="aicUpTitle">上传 Word / 文本文档</div>'
    + '<div class="aic-up-sub" id="aicUpSub">支持 .docx / .txt / .md / .pdf · 单文件 ≤ 20MB · 文档仅保存在本机</div>'
    + '<button class="btn-mini primary" id="aicPick">📎 选择文件</button>'
    + '</div>'
    + '<div class="aic-doc-meta" id="aicDocMeta" style="display:none;"></div>'
    + '</div>'
    + '<div class="aic-zhuque-state" id="aicZhuqueState"></div>'
    + '<div class="aic-result" id="aicResult"></div>'
    + '<div class="aic-progress" id="aicProgress" style="display:none;"></div>'
    + '</div>'
    + '<div class="uw-foot">'
    + '<span class="uw-foot-note" id="aicNote">检测只读，不修改论文任何内容</span>'
    + '<button class="btn-mini" id="aicDownload" style="display:none;" title="下载（改写后）文档：按原格式，未改写段落原样保留">📥 下载改写文档</button>'
    + '<button class="btn-mini" id="aicExport" style="display:none;">📊 导出报告</button>'
    + '<button class="btn-mini primary" id="aicRun">🚀 开始检测</button>'
    + '</div></div>';
  document.body.appendChild(modal);
  modal.querySelector("#aicClose").addEventListener("click", () => modal.classList.remove("open"));
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.classList.remove("open"); });
  modal.querySelector("#aicRun").addEventListener("click", runAiCenter);
  modal.querySelector("#aicExport").addEventListener("click", exportAiReport);
  modal.querySelector("#aicPick").addEventListener("click", () =>
    modal.querySelector("#aicFileInput").click());
  modal.querySelector("#aicFileInput").addEventListener("change", (e) => {
    uploadDocCheckFile(e.target.files?.[0]);
    e.target.value = "";
  });
  modal.querySelector("#aicDownload").addEventListener("click", downloadDocChecked);
  modal.addEventListener("change", (e) => {
    if (e.target.name === "aicMode") {
      resetAicDocState();   // 换检测模式：清空已导入文档，避免把报告模式的核验结果带到别的模式
      syncAicMode();
    }
  });
  AIC.modal = modal;
}

function resetAicDocState() {
  AIC.doc = null;
  AIC.verify = null;
  AIC.reportMarks = [];
  AIC.docReport = null;
  AIC.recheck = null;
  AIC.lastChanged = [];
  const res = AIC.modal?.querySelector("#aicResult");
  if (res) res.innerHTML = "";
  const ex = AIC.modal?.querySelector("#aicExport");
  if (ex) ex.style.display = "none";
  const dl = AIC.modal?.querySelector("#aicDownload");
  if (dl) dl.style.display = "none";
  const empty = AIC.modal?.querySelector("#aicUpEmpty");
  if (empty) empty.style.display = "";
  if (AIC.modal) renderAicDocMeta();
}

function syncAicMode() {
  const mode = aicModeValue();
  const needsFile = mode === "upload" || mode === "report";
  AIC.modal.querySelector("#aicUploadBox").style.display = needsFile ? "" : "none";
  const upTitle = AIC.modal.querySelector("#aicUpTitle");
  const upSub = AIC.modal.querySelector("#aicUpSub");
  if (upTitle) upTitle.textContent = mode === "report"
    ? "上传 AI 检测报告 / 已检测论文" : "上传 Word / 文本文档";
  if (upSub) upSub.textContent = mode === "report"
    ? "支持 .docx / .txt / .md / .pdf（朱雀报告 PDF 自动解析片段）· 先核验是否为当前任务论文 · 文档仅保存在本机"
    : "支持 .docx / .txt / .md / .pdf · 单文件 ≤ 20MB · 文档仅保存在本机";
  const runBtn = AIC.modal.querySelector("#aicRun");
  if (runBtn && !AIC.running) {
    runBtn.textContent = mode === "report" ? "🔎 提取 AI 严重部分" : "🚀 开始检测";
  }
  const note = AIC.modal.querySelector("#aicNote");
  if (mode === "report") {
    note.textContent = AIC.doc
      ? (AIC.verify
        ? (AIC.verify.ok === false && !AIC.verify.override
          ? "⚠ 核验未通过：请更换文档或手动确认继续"
          : (AIC.verify.ok === null ? "当前任务尚无成稿：未执行核验，仅可改写/下载"
                                    : "已核验通过：可提取 AI 严重段落并回写当前论文"))
        : "导入完成")
      : "导入后第一步：核验是否为当前任务论文";
  } else {
    note.textContent = mode !== "upload" ? "检测只读，不修改论文任何内容"
      : (AIC.doc ? `已加载《${AIC.doc.filename}》· ${AIC.doc.paras} 段 · ${AIC.doc.chars} 字`
                 : "上传文档后开始检测；改写仅作用于勾选段落，其余原样保留");
  }
}

function aicModeValue() {
  return AIC.modal.querySelector('input[name="aicMode"]:checked').value;
}

async function openAiCenter() {
  ensureAiCenterModal();
  AIC.modal.classList.add("open");
  refreshAicZhuqueState();
  syncAicMode();
}

async function refreshAicZhuqueState() {
  const el = AIC.modal.querySelector("#aicZhuqueState");
  try {
    const st = await api("/api/settings");
    const z = st.zhuque || {};
    el.className = "aic-zhuque-state " + (z.configured ? "ok" : "warn");
    el.innerHTML = z.configured
      ? `✓ 朱雀 API 已配置（${esc(z.masked)}）· 月免费 50 万 token`
      : `⚠ 朱雀 API 未配置：顶栏 ⚙ 设置 → 外部服务 填入 Key（仍可用本地引擎检测）`;
  } catch (e) { el.textContent = ""; }
}

/* ---------- 上传文档（word/txt/md/pdf）→ 解析段落 ---------- */
async function uploadDocCheckFile(file) {
  if (!file) return;
  if (aicModeValue() === "report") return uploadReportFile(file);
  const meta = AIC.modal.querySelector("#aicDocMeta");
  const empty = AIC.modal.querySelector("#aicUpEmpty");
  empty.style.display = "none";
  meta.style.display = "";
  meta.innerHTML = '<span class="ft-now-spin"></span> 上传解析中（提取文本 → 切分段落）…';
  try {
    const fd = new FormData();
    fd.append("file", file);
    const resp = await fetch("/api/doccheck/upload", { method: "POST", headers: authHeaders(), body: fd });
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try { detail = (await resp.json()).detail || detail; } catch (_) { /* 非JSON错误体 */ }
      throw new Error(detail);
    }
    const d = await resp.json();
    AIC.doc = d.doc;
    AIC.humanize = d.humanize;
    AIC.docReport = null;
    AIC.recheck = null;
    AIC.modal.querySelector("#aicResult").innerHTML = "";
    AIC.modal.querySelector("#aicExport").style.display = "none";
    AIC.modal.querySelector("#aicDownload").style.display = "none";
    renderAicDocMeta();
    syncAicMode();
    toast(`文档已解析：${d.doc.paras} 段 · ${d.doc.chars} 字，点击「开始检测」`, "ok");
  } catch (e) {
    AIC.doc = null;
    renderAicDocMeta();
    meta.innerHTML = `<span class="aic-doc-err">⚠ 上传失败：${esc(e.message)}</span>`;
    toast(`上传失败: ${e.message}`, "err");
  }
}

function renderAicDocMeta() {  const meta = AIC.modal.querySelector("#aicDocMeta");
  const empty = AIC.modal.querySelector("#aicUpEmpty");
  if (!AIC.doc) {
    meta.style.display = "none";
    meta.innerHTML = "";
    empty.style.display = "";
    return;
  }
  empty.style.display = "none";
  meta.style.display = "";
  const hz = AIC.humanize || {};
  meta.innerHTML =
    `<span class="aic-doc-ico">${AIC.doc.ext === ".pdf" ? "📕" : AIC.doc.ext === ".docx" ? "📘" : "📄"}</span>`
    + `<span class="aic-doc-name" title="${esc(AIC.doc.filename)}">${esc(AIC.doc.filename)}</span>`
    + `<span class="aic-doc-stats">${AIC.doc.paras} 段 · ${AIC.doc.chars} 字</span>`
    + `<span class="aic-doc-hz ${hz.available ? "ok" : "warn"}" title="${esc(hz.hint || "")}">`
    + (hz.available ? "✓ humanize-9b 在线" : "⚠ humanize-9b 离线") + "</span>"
    + '<span class="spacer"></span>'
    + '<button class="btn-mini" id="aicRePick">更换文档</button>';
  meta.querySelector("#aicRePick").addEventListener("click", () =>
    AIC.modal.querySelector("#aicFileInput").click());
}

/* ---------- 导入检测文档：第一步核验当前任务论文 ---------- */

function aicVerified() {
  return !!(AIC.verify && (AIC.verify.ok === true || AIC.verify.override));
}

function aicVerifyCard() {
  const v = AIC.verify;
  if (!v) return "";
  const pct = Math.round((Number(v.score) || 0) * 100);
  if (v.ok === true || v.override) {
    return `<div class="aic-verify ok">
      <div class="aic-vf-main">✓ 已核验：与当前任务论文${v.paper_title ? `《${esc(v.paper_title)}》` : ""}一致</div>
      <div class="aic-vf-sub">段落重合度 ${pct}% · 导入 ${v.imported_chars || 0} 字 / 当前论文 ${v.current_chars || 0} 字${v.override ? " · 已手动跳过核验" : ""}</div>
    </div>`;
  }
  if (v.ok === false) {
    return `<div class="aic-verify bad">
      <div class="aic-vf-main">⚠ 核验未通过：这份文档可能不是当前任务论文</div>
      <div class="aic-vf-sub">${esc(v.reason || "")} · 导入 ${v.imported_chars || 0} 字 / 当前论文 ${v.current_chars || 0} 字
        <button class="btn-mini" id="aicOverride" style="margin-left:8px;">仍要继续</button></div>
    </div>`;
  }
  return `<div class="aic-verify warn">
    <div class="aic-vf-main">ℹ 未执行论文核验（当前任务还没有成稿论文）</div>
    <div class="aic-vf-sub">${esc(v.reason || "")}</div>
  </div>`;
}

function bindAicAux() {
  AIC.modal.querySelector("#aicOverride")?.addEventListener("click", async () => {
    const ok = await ui.confirm({
      title: "跳过论文核验", icon: "⚠", danger: true,
      message: "该文档与当前任务论文重合度不足，继续改写/回写可能改到错误的论文上。确认仍要继续？",
      confirmText: "仍然继续",
    });
    if (!ok) return;
    AIC.verify = { ...(AIC.verify || {}), override: true };
    renderAicReportImport();
    syncAicMode();
  });
  AIC.modal.querySelector("#aicApplyPaper")?.addEventListener("click", applyDocToPaper);
}

function aicParaBarHtml() {
  const blocked = aicModeValue() === "report" && AIC.verify
    && AIC.verify.ok === false && !AIC.verify.override;
  const rewriteTitle = blocked
    ? "论文核验未通过：请更换文档或点「仍要继续」手动确认"
    : (esc(AIC.humanize?.hint || "") || "humanize-9b（Ollama 本地微调）句式级去AI改写，锁数字/引用，改写后自动复检");
  return `<div class="aic-para-bar">
    <button class="btn-mini" data-aicsel="smart" title="选中报告标记，或 BERT ≥50% / 启发式 ≥55 的段落">🎯 选中疑似段</button>
    <button class="btn-mini" data-aicsel="all">全选</button>
    <button class="btn-mini" data-aicsel="none">清空</button>
    <span class="aic-para-count" id="aicSelCount">已选 0 段</span>
    <span class="spacer"></span>
    <button class="btn-mini primary" id="aicRewrite" ${(AIC.humanize?.available && !blocked) ? "" : "disabled"}
      title="${rewriteTitle}">🪄 改写选中段并复检</button>
  </div>`;
}

async function uploadReportFile(file) {
  const meta = AIC.modal.querySelector("#aicDocMeta");
  const empty = AIC.modal.querySelector("#aicUpEmpty");
  empty.style.display = "none";
  meta.style.display = "";
  meta.innerHTML = '<span class="ft-now-spin"></span> 解析检测文档并核验当前任务论文…';
  AIC.modal.querySelector("#aicResult").innerHTML = "";
  try {
    const fd = new FormData();
    fd.append("file", file);
    const qs = state.currentProject
      ? `?project_name=${encodeURIComponent(state.currentProject)}` : "";
    const resp = await fetch(`/api/doccheck/import-report${qs}`,
                             { method: "POST", headers: authHeaders(), body: fd });
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try { detail = (await resp.json()).detail || detail; } catch (_) { /* 非 JSON 错误体 */ }
      throw new Error(detail);
    }
    const d = await resp.json();
    AIC.doc = d.doc;
    AIC.humanize = d.humanize;
    AIC.verify = d.verify || null;
    AIC.reportMarks = d.doc.marks || [];
    AIC.docReport = null;
    AIC.recheck = null;
    AIC.lastChanged = [];
    AIC.modal.querySelector("#aicExport").style.display = "none";
    AIC.modal.querySelector("#aicDownload").style.display = "none";
    renderAicDocMeta();
    renderAicReportImport();
    syncAicMode();
    const bad = d.verify && d.verify.ok === false;
    toast(bad ? "⚠ 论文核验未通过：这份文档可能不是当前任务论文"
              : `文档已导入：${d.doc.paras} 段 · 报告标记 ${AIC.reportMarks.length} 段`,
          bad ? "warn" : "ok");
  } catch (e) {
    AIC.doc = null;
    renderAicDocMeta();
    meta.style.display = "";
    meta.innerHTML = `<span class="aic-doc-err">⚠ 导入失败：${esc(e.message)}</span>`;
    toast(`导入失败: ${e.message}`, "err");
  }
}

function renderAicReportImport() {
  if (!AIC.doc) return;
  const doc = AIC.doc;
  const marks = AIC.reportMarks || [];
  const markMap = {};
  marks.forEach((m) => { markMap[m.idx] = m; });
  const stats = doc.para_stats || [];
  const previews = doc.previews || [];
  // 朱雀 PDF：按报告片段汇总（同一片段可能被切成多个改写块）
  const segBest = {};
  marks.forEach((m) => {
    if (m.segment == null) return;
    if (!segBest[m.segment] || (m.aigc || 0) > (segBest[m.segment].aigc || 0)) {
      segBest[m.segment] = m;
    }
  });
  const uniqSegs = Object.values(segBest);
  const isZhuquePdf = marks.some((m) => m.source === "zhuque_pdf");
  const nAI = uniqSegs.filter((m) => m.severity === "high").length;
  const nSus = uniqSegs.filter((m) => m.severity === "medium").length;
  const summary = isZhuquePdf
    ? `朱雀报告：AI 特征 ${nAI} 段 · 疑似 AI ${nSus} 段 · 已定位 ${marks.length} 个改写块`
    : `报告标记 ${marks.length} 段`;
  const paraScores = previews.map((p, i) => ({
    chars: (stats[i] || {}).chars ?? p.length,
    rewritable: (stats[i] || {}).rewritable !== false,
    ai_prob: null, heur: null, mark: markMap[i] || null,
  }));
  AIC.docReport = { para_scores: paraScores, paras: doc.paras, chars: doc.chars,
                    filename: doc.filename, marks };
  const rows = previews.map((p, i) =>
    _aicParaRow(i, paraScores[i], p, undefined, paraScores[i].mark)).join("");
  AIC.modal.querySelector("#aicResult").innerHTML = `
    ${aicVerifyCard()}
    <div class="aic-full">
      <div class="aic-full-title">${esc(doc.filename)}</div>
      <div class="aic-full-meta">${doc.paras} 段 · ${doc.chars} 字 · ${summary}</div>
      <div class="aic-hint">${marks.length
        ? (isZhuquePdf
          ? `已按朱雀报告的 AIGC 值提取 AI/疑似片段，并在当前论文中定位到对应真实段落（默认勾选）。改写后可直接回写。`
          : `已提取 ${marks.length} 个 AI 严重标记段落（文档高亮 / 警示色 / 关键词 / 表格百分比），已默认勾选，可直接用 9B 改写。`)
        : `报告未包含可识别的 AI 标记。点击底部「🔎 提取 AI 严重部分」运行三引擎检测，自动勾选高 AI 概率段落。`}</div>
      <div class="set-group-title">段落明细<span class="aic-para-title-sub">勾选后用微调 humanize-9b 改写并自动复检 · 徽标为报告 AIGC 值或 BERT AI 概率</span></div>
      ${aicParaBarHtml()}
      <div class="aic-paras" id="aicParas">${rows}</div>
    </div>`;
  bindAicParaEvents();
  AIC.modal.querySelectorAll("#aicParas input").forEach((cb) => {
    const s = paraScores[+cb.dataset.para] || {};
    if (s.mark && !cb.disabled) cb.checked = true;
  });
  const cnt = AIC.modal.querySelector("#aicSelCount");
  if (cnt) {
    cnt.textContent = `已选 ${AIC.modal.querySelectorAll("#aicParas input:checked").length} 段`;
  }
}

async function runDocDetectAndRender(autoSelect) {
  const prog = AIC.modal.querySelector("#aicProgress");
  prog.style.display = "";
  prog.innerHTML = '<span class="ft-now-spin"></span>整篇送检中（朱雀合并口径 + 本地 BERT + 段落级评分），约 20~90 秒…';
  const r = await api("/api/doccheck/detect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_id: AIC.doc.id }),
  });
  AIC.doc = r.doc;
  AIC.docReport = r.report;
  AIC.recheck = null;
  if (AIC.reportMarks?.length) {   // 报告标记并入段落评分，保留徽标
    const mm = {};
    AIC.reportMarks.forEach((m) => { mm[m.idx] = m; });
    (r.report.para_scores || []).forEach((s, i) => { if (mm[i]) s.mark = mm[i]; });
  }
  AIC.lastReport = { mode: "doc", doc: AIC.doc, report: r.report, recheck: null };
  AIC.modal.querySelector("#aicResult").innerHTML = renderAicDoc(r.report, null, []);
  bindAicParaEvents();
  if (autoSelect) AIC.modal.querySelector('[data-aicsel="smart"]')?.click();
  return r;
}

async function applyDocToPaper() {
  if (!AIC.doc || !state.currentProject) return;
  const n = (AIC.lastChanged || []).length;
  if (!n) { toast("没有可回写的改写段落", "err"); return; }
  const ok = await ui.confirm({
    title: "应用到当前论文", icon: "📌",
    message: `将把 ${n} 段 9B 改写结果替换到当前论文《${state.currentProject}》的对应段落，并立即保存大纲。建议先确认正文没有未保存的修改。`,
    confirmText: "应用并保存",
  });
  if (!ok) return;
  const btn = AIC.modal.querySelector("#aicApplyPaper");
  if (btn) { btn.disabled = true; btn.textContent = "应用中…"; }
  try {
    const r = await api("/api/doccheck/apply-to-paper", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ doc_id: AIC.doc.id, project_name: state.currentProject }),
    });
    toast(`已应用到当前论文：${r.applied} 段成功` +
          (r.skipped ? ` · ${r.skipped} 段未定位` : ""), "ok");
    AIC.lastChanged = [];
    AIC.modal.querySelector("#aicApplyPaper")?.closest(".aic-apply-bar")?.remove();
    await tryLoadOutline(state.currentProject);
    renderOutline();
    renderPaperView();
    scheduleSave();
  } catch (e) {
    toast(`应用失败: ${e.message}`, "err");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "📌 应用到当前论文"; }
  }
}

async function downloadDocChecked() {
  if (!AIC.doc) return;
  try {
    const resp = await fetch(`/api/doccheck/doc/${encodeURIComponent(AIC.doc.id)}/download`,
                             { headers: authHeaders() });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const disp = resp.headers.get("Content-Disposition") || "";
    const m = disp.match(/filename\*?=(?:UTF-8'')?"?([^";]+)/i);
    let name = `${AIC.doc.filename.replace(/\.[^.]+$/, "")}_改写版`;
    if (m) { try { name = decodeURIComponent(m[1]); } catch (_) { name = m[1]; } }
    const blob = await resp.blob();
    const objUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objUrl;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(objUrl);
    toast("改写文档已下载", "ok");
  } catch (e) {
    toast(`下载失败: ${e.message}`, "err");
  }
}

async function runAiCenter() {
  if (AIC.running) return;
  const mode = aicModeValue();
  if ((mode === "upload" || mode === "report") && !AIC.doc) {
    AIC.modal.querySelector("#aicPick").click();
    return;
  }
  if ((mode === "full" || mode === "sections") && !state.currentProject) {
    toast("请先选择项目", "err"); return;
  }
  if (mode === "report" && AIC.verify && AIC.verify.ok === false && !AIC.verify.override) {
    toast("文档未通过论文核验：请更换文档，或点「仍要继续」手动确认", "err");
    return;
  }
  const btn = AIC.modal.querySelector("#aicRun");
  const prog = AIC.modal.querySelector("#aicProgress");
  const result = AIC.modal.querySelector("#aicResult");
  AIC.running = true;
  btn.disabled = true; btn.textContent = "处理中…";
  AIC.modal.querySelector("#aicExport").style.display = "none";
  AIC.modal.querySelector("#aicDownload").style.display = "none";
  prog.style.display = "";
  try {
    if (mode === "full") {
      result.innerHTML = "";
      prog.innerHTML = '<span class="ft-now-spin"></span>整篇送检中（朱雀合并口径 + 本地 BERT），约 20~60 秒…';
      const r = await fetch(`/api/aicenter/full?project_name=${encodeURIComponent(state.currentProject)}`,
                            { headers: authHeaders(), signal: AbortSignal.timeout(300000) }).then(x => {
        if (!x.ok) throw new Error("HTTP " + x.status);
        return x.json();
      });
      AIC.lastReport = { mode: "full", project: state.currentProject, full: r, sections: null };
      result.innerHTML = renderAicFull(r);
      AIC.modal.querySelector("#aicExport").style.display = "";
      toast("AI 检测完成", "ok");
    } else if (mode === "sections") {
      const secs = (state.outline?.sections || []).filter(s => (s.content || "").trim());
      if (!secs.length) throw new Error("没有成稿章节");
      result.innerHTML = "";
      const rows = [];
      for (let i = 0; i < secs.length; i++) {
        prog.innerHTML = `<span class="ft-now-spin"></span>逐节检测中 ${i + 1}/${secs.length}：「${esc(secs[i].title)}」`;
        const r = await api(`/api/sections/ai_check?project_name=${encodeURIComponent(state.currentProject)}&section_id=${encodeURIComponent(secs[i].id)}`);
        rows.push({ title: secs[i].title, words: secs[i].content.length, r });
      }
      AIC.lastReport = { mode: "sections", project: state.currentProject, full: null, sections: rows };
      result.innerHTML = renderAicSections(rows);
      AIC.modal.querySelector("#aicExport").style.display = "";
      toast("AI 检测完成", "ok");
    } else if (mode === "upload") {
      result.innerHTML = "";
      await runDocDetectAndRender(false);
      AIC.modal.querySelector("#aicExport").style.display = "";
      toast("AI 检测完成", "ok");
    } else {
      // 导入检测文档：优先用报告自带标记；没有标记则跑三引擎自动提取
      const hasScores = (AIC.docReport?.para_scores || []).some(s => s.ai_prob != null);
      if (AIC.reportMarks?.length && !hasScores) {
        AIC.modal.querySelector('[data-aicsel="smart"]')?.click();
        const n = AIC.modal.querySelectorAll("#aicParas input:checked").length;
        toast(n ? `已按检测报告标记选中 ${n} 段，可直接用 9B 改写`
                : "报告没有可改写的标记段落", n ? "ok" : "warn");
      } else {
        result.innerHTML = "";
        await runDocDetectAndRender(true);
        AIC.modal.querySelector("#aicExport").style.display = "";
        toast("AI 严重部分已提取并自动勾选", "ok");
      }
    }
  } catch (e) {
    result.innerHTML = `<div class="aic-error">处理失败：${esc(e.message)}</div>`;
  } finally {
    AIC.running = false;
    btn.disabled = false;
    btn.textContent = aicModeValue() === "report" ? "🔎 提取 AI 严重部分" : "🚀 开始检测";
    prog.style.display = "none";
    syncAicMode();
  }
}

function _zqBadge(zq, available) {
  if (!zq) {
    const why = available === false ? "未配置 API Key"
      : available === true ? "本次请求失败（限流/额度不足），已用本地引擎完成检测"
      : "未配置 API Key";
    return `<div class="aic-zq-card na"><div class="zqc-top"><span class="zqc-name">朱雀官方检测</span></div><div class="aic-hint">${why}</div></div>`;
  }
  const ai = Math.round((zq.ai_ratio || 0) * 100), su = Math.round((zq.suspect_ratio || 0) * 100);
  const hu = Math.round((zq.human_ratio || 0) * 100);
  // 判定以 AI 确证占比为硬指标（朱雀“疑似”是灰区标签，正常学术文本也常偏高）
  const cls = ai >= 50 ? "ai" : ai >= 20 ? "suspect" : "human";
  const verdict = cls === "human" ? "判定人类写作" : cls === "suspect" ? "疑似（临界）" : "判定 AI 生成";
  return `<div class="aic-zq-card ${cls}">
    <div class="zqc-top"><span class="zqc-name">朱雀官方检测</span><span class="aic-tag ${cls}">${verdict}</span></div>
    <div class="zqc-big"><span class="zb-ai">${ai}<i>%</i></span><span class="zb-lab">AI 生成占比</span></div>
    <div class="zqc-stack" role="img" aria-label="AI ${ai}% 疑似 ${su}% 人工 ${hu}%">
      <div class="zs-ai" style="width:${Math.max(ai, ai > 0 ? 2 : 0)}%">${ai > 8 ? ai + "%" : ""}</div>
      <div class="zs-sus" style="width:${Math.max(su, su > 0 ? 2 : 0)}%">${su > 10 ? su + "%" : ""}</div>
      <div class="zs-hum" style="width:${Math.max(hu, hu > 0 ? 2 : 0)}%">${hu > 12 ? hu + "%" : ""}</div>
    </div>
    <div class="zqc-legend"><span class="zl-ai">AI ${ai}%</span><span class="zl-sus">疑似 ${su}%</span><span class="zl-hum">人工 ${hu}%</span></div>
  </div>`;
}

function renderAicFull(r) {
  const mc = r.model_check;
  const hs = r.heuristic?.score ?? 0;
  return `
  <div class="aic-full">
    <div class="aic-full-title">${esc(r.paper_title || "")}</div>
    <div class="aic-full-meta">${r.sections} 节 · ${r.chars} 字 · ${esc((r.checked_at || "").replace("T", " "))}</div>
    <div class="set-group-title">朱雀官方检测（合并口径 · 与网页版算法一致）</div>
    ${_zqBadge(r.zhuque, r.zhuque_available)}
    ${r.zhuque ? `<div class="aic-hint">消耗 ${r.zhuque.tokens || "-"} tokens（月免费额度 50 万）</div>` : ""}
    <div class="set-group-title">本地 BERT 引擎 + 启发式体检</div>
    <div class="aic-duo">
      ${mc ? `<div class="duo-card ${mc.verdict === "human" ? "human" : mc.verdict}">
        <div class="duo-num">${Math.round(mc.ai_prob * 100)}<i>%</i></div>
        <div class="duo-lab">本地 BERT · AI 概率</div>
        <div class="duo-verdict">${mc.verdict === "human" ? "人类写作" : mc.verdict === "suspect" ? "疑似" : "AI 生成"}</div></div>` : ""}
      <div class="duo-card ${hs > 55 ? "ai" : hs > 35 ? "suspect" : "human"}">
        <div class="duo-num">${r.heuristic?.score ?? "-"}<i>/100</i></div>
        <div class="duo-lab">启发式体检 · AI 味分</div>
        <div class="duo-verdict">${hs > 55 ? "需去AI味" : "自然"}</div></div>
    </div>
  </div>`;
}

function renderAicSections(rows) {
  const lines = rows.map(({ title, words, r }) => {
    const zq = r.zhuque_check;
    const ai = zq ? Math.round((zq.ai_ratio || 0) * 100) : null;
    const su = zq ? Math.round((zq.suspect_ratio || 0) * 100) : null;
    const mc = r.model_check ? Math.round(r.model_check.ai_prob * 100) : null;
    const cell = (v, hard) => v === null ? "-" :
      `<b class="${hard ? (v >= 50 ? "tc-red" : v >= 20 ? "tc-org" : "tc-grn") : (v >= 55 ? "tc-red" : v >= 35 ? "tc-org" : "tc-grn")}">${v}%</b>`;
    return `<tr><td>${esc(title)}</td><td>${words}</td><td>${cell(r.score ?? null, false)}</td>`
      + `<td>${cell(ai, true)}</td><td>${su === null ? "-" : su + "%"}</td><td>${cell(mc, true)}</td></tr>`;
  }).join("");
  return `<table class="aic-table"><thead><tr><th>章节</th><th>字数</th><th>启发式</th><th>朱雀AI</th><th>朱雀疑似</th><th>本地BERT</th></tr></thead><tbody>${lines}</tbody></table>`
    + `<div class="aic-hint">朱雀「疑似」是灰区标签（正常学术文本也常见），硬指标看「朱雀AI」列；≥50% 时建议对该节执行「🪄 去AI味·深度」后复检</div>`;
}

/* ---------- 上传文档：报告渲染 + 段落勾选 + 9B 改写复检 ---------- */
function _aicParaRow(i, score, preview, rewritten, mark) {
  const prob = score?.ai_prob == null ? null : Math.round(score.ai_prob * 100);
  const cls = prob == null ? "na" : prob >= 70 ? "ai" : prob >= 50 ? "suspect" : "human";
  const heur = score?.heur;
  const m = mark || score?.mark;
  const mLab = m
    ? (m.aigc != null
      ? `${m.severity === "high" ? "AI" : "疑似"} ${Number(m.aigc).toFixed(2)}`
      : "AI标记")
    : "";
  const st = rewritten === true ? '<span class="aic-para-st ok">✅ 已改写</span>'
    : rewritten === false ? '<span class="aic-para-st fail">❌ 保留原文</span>'
    : '<span class="aic-para-st" data-st="' + i + '"></span>';
  return `<label class="aic-para ${cls !== "na" ? cls : ""} ${m ? "marked" : ""}" data-idx="${i}">
    <input type="checkbox" data-para="${i}" ${score?.rewritable ? "" : "disabled"
      } title="${score?.rewritable ? "" : "段落过短或为标题/表格行，不参与改写"}">
    <span class="aic-para-no">${i + 1}</span>
    <span class="aic-para-badge ${cls}" title="本地 BERT · AI 概率">${prob == null ? "–" : prob + "%"}</span>
    <span class="aic-para-heur" title="启发式 AI 味分/100">${heur == null ? "-" : heur}</span>
    ${m ? `<span class="aic-para-marktag ${esc(m.severity || "")}" title="${esc(m.reason || "检测报告标记")}">${esc(mLab)}</span>` : ""}
    <span class="aic-para-pv">${esc(preview || "")}</span>
    ${st}
  </label>`;
}

function aicCmpTable(before, after) {
  const pct = (v) => v == null ? null : Math.round(v * 100);   // 比例 → 整数百分数
  const row = (name, b, a, unit = "%", goodDown = true) => {
    const hasNum = b != null && a != null;
    const delta = hasNum ? a - b : null;
    const better = delta == null || delta === 0 ? "" : (delta < 0) === goodDown ? "↓" : "↑";
    const dcls = delta == null || delta === 0 ? "" : (delta < 0) === goodDown ? "down" : "up";
    const fmt = (v) => v == null ? "-" : v + unit;
    return `<tr><td>${name}</td><td>${fmt(b)}</td><td><b>${fmt(a)}</b></td>`
      + `<td class="aic-cmp-d ${dcls}">${delta == null ? "-" : (delta > 0 ? "+" : "") + delta + (better || "")}</td></tr>`;
  };
  const bz = before?.zhuque, az = after?.zhuque;
  const bm = before?.model_check, am = after?.model_check;
  const hb = before?.heuristic?.score, ha = after?.heuristic?.score;
  return `<div class="aic-cmp">
    <div class="aic-cmp-title">🔄 改写前后对比（改写段已替换，其余原样）</div>
    <table class="aic-table aic-cmp-table"><thead><tr><th>引擎指标</th><th>改写前</th><th>改写后</th><th>变化</th></tr></thead><tbody>
    ${row("朱雀 AI 确证占比", pct(bz?.ai_ratio), pct(az?.ai_ratio))}
    ${row("朱雀 疑似占比", pct(bz?.suspect_ratio), pct(az?.suspect_ratio))}
    ${row("朱雀 人工占比", pct(bz?.human_ratio), pct(az?.human_ratio), "%", false)}
    ${row("本地 BERT AI 概率", pct(bm?.ai_prob), pct(am?.ai_prob))}
    ${row("启发式 AI 味分", hb == null ? null : hb, ha == null ? null : ha, "/100")}
    </tbody></table>
    ${az ? `<div class="aic-hint">复检消耗 ${az.tokens || "-"} tokens · ${esc((after.checked_at || "").replace("T", " "))}</div>` : ""}
  </div>`;
}

function renderAicDoc(r, cmpBefore, rewrittenMap) {
  const mc = r.model_check;
  const hs = r.heuristic?.score ?? 0;
  const previews = AIC.doc?.previews || [];
  const rw = rewrittenMap || {};
  const rows = (r.para_scores || []).map((s, i) =>
    _aicParaRow(i, s, previews[i], rw[i], s.mark)).join("");
  const canApply = aicModeValue() === "report" && aicVerified() && (AIC.lastChanged || []).length;
  return `
  ${aicVerifyCard()}
  <div class="aic-full">
    ${cmpBefore ? aicCmpTable(cmpBefore, r) : `
    <div class="aic-full-title">${esc(r.filename || AIC.doc?.filename || "")}</div>
    <div class="aic-full-meta">${r.paras} 段 · ${r.chars} 字 · ${esc((r.checked_at || "").replace("T", " "))}</div>`}
    <div class="set-group-title">朱雀官方检测（合并口径 · 与网页版算法一致）</div>
    ${_zqBadge(r.zhuque, r.zhuque_available)}
    ${r.zhuque ? `<div class="aic-hint">消耗 ${r.zhuque.tokens || "-"} tokens（月免费额度 50 万）</div>` : ""}
    <div class="set-group-title">本地 BERT 引擎 + 启发式体检</div>
    <div class="aic-duo">
      ${mc ? `<div class="duo-card ${mc.verdict === "human" ? "human" : mc.verdict}">
        <div class="duo-num">${Math.round(mc.ai_prob * 100)}<i>%</i></div>
        <div class="duo-lab">本地 BERT · AI 概率</div>
        <div class="duo-verdict">${mc.verdict === "human" ? "人类写作" : mc.verdict === "suspect" ? "疑似" : "AI 生成"}</div></div>` : ""}
      <div class="duo-card ${hs > 55 ? "ai" : hs > 35 ? "suspect" : "human"}">
        <div class="duo-num">${r.heuristic?.score ?? "-"}<i>/100</i></div>
        <div class="duo-lab">启发式体检 · AI 味分</div>
        <div class="duo-verdict">${hs > 55 ? "需去AI味" : "自然"}</div></div>
    </div>
    <div class="set-group-title">段落明细<span class="aic-para-title-sub">勾选后用微调 humanize-9b 改写并自动复检 · 徽标为报告标记或 BERT AI 概率</span></div>
    ${aicParaBarHtml()}
    <div class="aic-paras" id="aicParas">${rows}</div>
    ${canApply ? `<div class="aic-apply-bar">
      <button class="btn-mini primary" id="aicApplyPaper">📌 应用到当前论文</button>
      <span class="aic-hint">把 ${AIC.lastChanged.length} 段改写结果替换到当前论文对应段落（已通过核验）</span>
    </div>` : ""}
  </div>`;
}

function bindAicParaEvents() {
  const box = AIC.modal.querySelector("#aicParas");
  if (!box) return;
  const syncCount = () => {
    const n = box.querySelectorAll("input:checked").length;
    const el = AIC.modal.querySelector("#aicSelCount");
    if (el) el.textContent = `已选 ${n} 段`;
  };
  box.addEventListener("change", syncCount);
  AIC.modal.querySelectorAll("[data-aicsel]").forEach((b) =>
    b.addEventListener("click", () => {
      const m = b.dataset.aicsel;
      box.querySelectorAll("input").forEach((cb) => {
        if (cb.disabled) return;
        if (m === "all") cb.checked = true;
        else if (m === "none") cb.checked = false;
        else {
          const s = (AIC.docReport?.para_scores || [])[+cb.dataset.para] || {};
          cb.checked = !!s.mark
                    || (s.ai_prob != null && s.ai_prob >= 0.5)
                    || (s.heur != null && s.heur >= 55);
        }
      });
      syncCount();
    }));
  AIC.modal.querySelector("#aicRewrite")?.addEventListener("click", runDocRewrite);
  bindAicAux();
  syncCount();
}

async function runDocRewrite() {
  if (AIC.rewriting) return;
  if (aicModeValue() === "report" && AIC.verify
      && AIC.verify.ok === false && !AIC.verify.override) {
    toast("论文核验未通过：请更换文档，或点「仍要继续」手动确认", "err");
    return;
  }
  const idxs = [...AIC.modal.querySelectorAll("#aicParas input:checked")].map((cb) => +cb.dataset.para);
  if (!idxs.length) { toast("请先勾选要改写的段落", "err"); return; }
  if (!AIC.humanize?.available) {
    toast("humanize-9b 不可用：" + (AIC.humanize?.hint || "请启动 Ollama"), "err");
    return;
  }
  AIC.rewriting = true;
  const btn = AIC.modal.querySelector("#aicRewrite");
  const prog = AIC.modal.querySelector("#aicProgress");
  const setSt = (i, html, cls) => {
    const el = AIC.modal.querySelector(`[data-st="${i}"]`);
    if (el) { el.innerHTML = html; el.className = "aic-para-st " + (cls || ""); }
  };
  btn.disabled = true;
  prog.style.display = "";
  prog.innerHTML = '<span class="ft-now-spin"></span>humanize-9b 启动中（本地模型加载约 10~30 秒）…';
  let done = 0;
  const rewritten = {};
  const beforeReport = AIC.docReport;
  try {
    await streamSSE("/api/doccheck/rewrite", { doc_id: AIC.doc.id, paras: idxs }, (ev) => {
      if (ev.type === "para_start") {
        prog.innerHTML = `<span class="ft-now-spin"></span>humanize-9b 改写中 ${done + 1}/${idxs.length}：第 ${ev.idx + 1} 段（${ev.chars} 字，本地模型约 10~60 秒/段）…`;
        setSt(ev.idx, "⏳ 改写中", "run");
      } else if (ev.type === "para_done") {
        done++;
        if (ev.ok) { rewritten[ev.idx] = true; setSt(ev.idx, `✅ 已改写 ${ev.after_chars}字 · ${ev.secs}s`, "ok"); }
        else { rewritten[ev.idx] = false; setSt(ev.idx, "❌ 保留原文", "fail"); }
      } else if (ev.type === "status") {
        prog.innerHTML = `<span class="ft-now-spin"></span>${esc(ev.content)}`;
      } else if (ev.type === "recheck") {
        AIC.recheck = ev.report;
      } else if (ev.type === "done") {
        AIC.doc = ev.doc;
        AIC.docReport = AIC.recheck;
        AIC.lastChanged = ev.changed || [];
        AIC.lastReport = { mode: "doc", doc: AIC.doc, report: beforeReport,
                           recheck: AIC.recheck, rewritten, changed: ev.changed };
        const res = AIC.modal.querySelector("#aicResult");
        res.innerHTML = renderAicDoc(AIC.recheck, beforeReport, rewritten);
        bindAicParaEvents();
        AIC.modal.querySelector("#aicExport").style.display = "";
        AIC.modal.querySelector("#aicDownload").style.display = "";
        toast(`9B 改写完成：${ev.changed.length} 段成功` +
              (ev.failed.length ? ` · ${ev.failed.length} 段未过锁字校验保留原文` : ""), "ok");
        prog.innerHTML = "✅ 改写并复检完成（对比见上方「改写前后对比」）";
        if (aicModeValue() === "report" && aicVerified() && ev.changed.length) {
          prog.innerHTML += " · 可点「📌 应用到当前论文」回写";
        }
      } else if (ev.type === "error") {
        throw new Error(ev.content);
      }
    });
  } catch (e) {
    toast(`改写失败: ${e.message}`, "err");
    prog.innerHTML = "⚠ " + esc(e.message);
  } finally {
    AIC.rewriting = false;
    btn.disabled = false;
    setTimeout(() => { if (!AIC.rewriting) prog.style.display = "none"; }, 5000);
  }
}

function exportAiReport() {
  if (!AIC.lastReport) return;
  const rep = AIC.lastReport;
  const t = new Date();
  const stamp = `${t.getFullYear()}${String(t.getMonth() + 1).padStart(2, "0")}${String(t.getDate()).padStart(2, "0")}_${String(t.getHours()).padStart(2, "0")}${String(t.getMinutes()).padStart(2, "0")}`;
  let md = `# AI 检测报告 · ${rep.project || rep.doc?.filename || ""}\n\n- 检测时间：${t.toLocaleString()}\n- 检测模式：${rep.mode === "full" ? "整篇送检（朱雀合并口径）" : rep.mode === "doc" ? "上传文档检测" : "逐节检测"}\n\n`;
  if (rep.mode === "full" && rep.full) {
    const f = rep.full, zq = f.zhuque;
    md += `## 整篇结果（${f.sections} 节 · ${f.chars} 字）\n\n`;
    md += `| 引擎 | AI | 疑似 | 人工 | 判定 |\n|---|---|---|---|---|\n`;
    if (zq) md += `| 朱雀官方API | ${Math.round(zq.ai_ratio * 100)}% | ${Math.round(zq.suspect_ratio * 100)}% | ${Math.round(zq.human_ratio * 100)}% | ${zq.ai_ratio >= 0.5 ? "AI 生成" : zq.ai_ratio >= 0.2 ? "疑似" : "人类写作"} |\n`;
    if (f.model_check) md += `| 本地BERT | ${Math.round(f.model_check.ai_prob * 100)}% | - | - | ${f.model_check.verdict === "human" ? "人类写作" : f.model_check.verdict === "suspect" ? "疑似" : "AI 生成"} |\n`;
    md += `| 启发式体检 | ${f.heuristic?.score ?? "-"}/100 | - | - | 阈值55 |\n\n`;
    if (zq?.tokens) md += `> 朱雀消耗 ${zq.tokens} tokens（月免费 50 万）\n`;
  } else if (rep.mode === "doc" && rep.report) {
    const f = rep.report, zq = f.zhuque;
    md += `## 上传文档《${rep.doc?.filename || ""}》（${f.paras} 段 · ${f.chars} 字）\n\n`;
    const engineRows = (r, label) => {
      let s = `| ${label} | ${r?.zhuque ? Math.round(r.zhuque.ai_ratio * 100) + "%" : "-"} | ${r?.zhuque ? Math.round(r.zhuque.suspect_ratio * 100) + "%" : "-"} | ${r?.zhuque ? Math.round(r.zhuque.human_ratio * 100) + "%" : "-"} | ${r?.model_check ? Math.round(r.model_check.ai_prob * 100) + "%" : "-"} | ${r?.heuristic?.score ?? "-"} |\n`;
      return s;
    };
    md += `| 引擎 | 朱雀AI | 朱雀疑似 | 朱雀人工 | 本地BERT | 启发式 |\n|---|---|---|---|---|---|\n`;
    if (rep.recheck) {
      md += engineRows(f, "改写前");
      md += engineRows(rep.recheck, "改写后");
      if (rep.changed) md += `\n> humanize-9b 改写段落：${rep.changed.map((i) => i + 1).join("、") || "无"}\n`;
    } else {
      md += engineRows(f, "检测结果");
    }
    md += `\n### 段落明细（BERT AI 概率 / 启发式分）\n\n| # | 字数 | BERT | 启发式 | 状态 |\n|---|---|---|---|---|\n`;
    (f.para_scores || []).forEach((s, i) => {
      const rw = rep.rewritten?.[i];
      md += `| ${i + 1} | ${s.chars} | ${s.ai_prob == null ? "-" : Math.round(s.ai_prob * 100) + "%"} | ${s.heur ?? "-"} | ${rw === true ? "已改写" : rw === false ? "保留原文" : "-"} |\n`;
    });
    md += `\n`;
  } else if (rep.sections) {
    md += `| 章节 | 字数 | 启发式 | 朱雀AI | 朱雀疑似 | 本地BERT |\n|---|---|---|---|---|---|\n`;
    for (const { title, words, r } of rep.sections) {
      const zq = r.zhuque_check, mc = r.model_check;
      md += `| ${title} | ${words} | ${r.score ?? "-"} | ${zq ? Math.round(zq.ai_ratio * 100) + "%" : "-"} | ${zq ? Math.round(zq.suspect_ratio * 100) + "%" : "-"} | ${mc ? Math.round(mc.ai_prob * 100) + "%" : "-"} |\n`;
    }
    md += `\n> 硬指标看「朱雀AI」列；≥50% 建议对该节执行「🪄 去AI味·深度」后复检\n`;
  }
  md += `\n---\n引擎说明：朱雀官方API（腾讯 EdgeOne，合并口径与网页版一致）· 本地BERT（AIGC_detector_zhv2）· 启发式体检（套话/节奏统计）\n`;
  const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `AI检测报告_${rep.project}_${stamp}.md`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast("报告已导出（Markdown）", "ok");
}

/* ================= 初始化 ================= */
async function init() {
  // URL 主题参数（?__theme=codex/qoder/...）：一次性以链接指定主题，便于分享与截图测试
  const __th = new URLSearchParams(location.search).get("__theme");
  if (__th && THEMES.includes(__th)) applyTheme(__th);
  else applyTheme("dark");  // 默认主题：墨岩（无保存状态时）
  applyUiPrefs();
  // URL 直达设置分区（#__sp=appearance/model/...）
  const __sp = (location.hash || "").replace("#__sp=", "");
  if (__sp) {
    openSettings().then(() => {
      const item = document.querySelector(`.sp-nav-item[data-sp="${__sp}"]`);
      item && item.click();
    });
  }
  bindAuthEvents();
  await initAuth();
  bindStaticEvents();
  bindShellExtras();
  bindLeftToggle();
  restoreLeftCollapsed();
  initSplitters();
  enhanceAllSelects();
  // 论文工作区：标签切换（论文/大纲）+ 头部菜单
  bindWorkspaceTabs();
  const _sb = $("#btnSettings");
  _sb && _sb.addEventListener("click", openSettings);
  bindWorkspaceMenus();
  bindSelPop();
  bindStrengthMenu();
  bindFullMenu();
  bindStyleMenu();
  bindPaperEdit();
  applyPaperStyle();
  await refreshSystemInfo();
  await loadTemplates();
  const ws = await loadWorkspaceState();
  await loadProjects();
  await refreshRefList();
  await restoreWorkspace(ws);
  restoreLeftCollapsed();
  if (!state.sessions.length) addSession(); // 没有历史时才开默认会话
  // 无项目/无大纲时的初始视图：大纲空状态提示 + 论文页准备向导
  // （restoreWorkspace 未命中项目时不会触发任何渲染，需在此兜底）
  if (!state.currentProject) { renderOutline(); renderPaperView(); }
  // 首次使用（未设置工作区根目录）：引导用户选择任意项目父目录
  if (!state.workspaceRoot) {
    toast("首次使用：请先设置工作区根目录（扫描你的研究项目，任意目录均可）");
    setWorkspaceRoot();
  }
}

/* ================= 工作区持久化 ================= */
async function loadWorkspaceState() {
  try {
    const resp = await api("/api/workspace");
    state.workspaceRoot = resp.workspace_root || "";
    state.wsRev = (resp.state && resp.state.rev) || 0;  // 状态版本号（乐观锁）
    const rootEl = $("#wsRootPath");
    if (rootEl) {
      rootEl.textContent = state.workspaceRoot ? `workspace: ${state.workspaceRoot}` : "";
      rootEl.title = state.workspaceRoot || "";
    }
    return resp;
  } catch (e) { return null; }
}

async function restoreWorkspace(ws) {
  const st = ws?.state;
  if (!st || !Object.keys(st).length) return;
  if (st.workspaces && typeof st.workspaces === "object") {
    state.wsHistory = st.workspaces;  // 各工作区快照（切换根目录时恢复用）
  }
  if (st.total_words) $("#totalWords").value = st.total_words;
  if (st.template) { try { $("#templateSelect").value = st.template; } catch (e) { /* ignore */ } }
  if (st.domain) { try { $("#domainSelect").value = st.domain; } catch (e) { /* ignore */ } }
  if (st.theme) applyTheme(st.theme);
  if (st.paper_style && typeof st.paper_style === "object") {
    state.paperStyle = { ...PAPER_STYLE_DEFAULTS, ...st.paper_style };
    applyPaperStyle();
  }
  if (st.pane_widths) {
    ["left", "preview", "chat", "right"].forEach((n) => {
      const w = Number(st.pane_widths[n]);
      if (w >= PANE_LIMITS[n].min && w <= PANE_LIMITS[n].max) applyPaneWidth(n, w);
    });
  }
  if (st.preview_open && window._lastPreviewPath) {
    openFilePreview(window._lastPreviewPath, window._lastPreviewName, true);
  }
  if (st.reference_paper) {
    try {
      $("#refSelect").value = st.reference_paper;
      syncRefSelectTitle();
      loadTemplatePanel();  // 恢复上次选择的模板论文档案面板
    } catch (e) { /* ignore */ }
  }
  if (Array.isArray(st.sessions) && st.sessions.length) {
    state.sessions = st.sessions.map((s, i) => ({
      id: s.id || `chat_${Date.now().toString(36)}_${i}`,
      name: s.name || `会话 ${i + 1}`,
      messages: Array.isArray(s.messages) ? s.messages : [],
      attachments: [],
      pendingSelections: [],
      ts: s.ts || (Date.now() - i * 60e3),
      pinned: !!s.pinned, pinnedAt: s.pinnedAt || undefined,
      archived: !!s.archived, archivedAt: s.archivedAt || undefined,
      project: typeof s.project === "string" ? s.project : null,
    }));
    state.activeSession = Math.min(Number(st.active_session) || 0, state.sessions.length - 1);
    renderSessionBar();
    renderChat();
  }
  if (Array.isArray(st.hidden_projects)) {
    state.hiddenProjects = st.hidden_projects.filter((n) => typeof n === "string");
  }
  if (st.active_project) {
    const known = (state.projects.registered || []).find(r => r.name === st.active_project)
      || (state.projects.candidates || []).find(c => c.name === st.active_project);
    // 只自动恢复位于当前工作区根目录下的项目：切根残留的他区项目不再被带回
    if (known && projectInRoot(known)) await selectProject(st.active_project, known.path, true, true);
  }
  // 隐藏项目（旧版 localStorage 记录迁移到工作区状态）
  if (!state.hiddenProjects.length) {
    try {
      const legacy = JSON.parse(localStorage.getItem("moji.hiddenProjects")) || [];
      if (Array.isArray(legacy) && legacy.length) {
        state.hiddenProjects = legacy;
        scheduleSave();
      }
    } catch (e) { /* ignore */ }
  }
  renderProjects();
  renderChat();  // 项目恢复完成后重绘聊天区：引导卡的"当前项目"文案不再停留旧值
}

let _wsSaveTimer = null;
function scheduleSave() { clearTimeout(_wsSaveTimer); _wsSaveTimer = setTimeout(() => saveWorkspaceNow(false), 800); }

/* 残留旧标签处理：版本落后且根目录已变 → 回到前台时整页刷新到当前工作区 */
let _staleTabReloadBound = false;
function _markStaleTab() {
  if (document.visibilityState === "visible") { location.reload(); return; }
  if (_staleTabReloadBound) return;
  _staleTabReloadBound = true;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") location.reload();
  }, { once: true });
}

let _wsSaveRetry = false;
async function saveWorkspaceNow(keepalive) {
  try {
    const resp = await fetch("/api/workspace", {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        rev: state.wsRev || 0,  // 乐观锁版本号：旧标签的保存会被服务端 409 拒绝
        workspace_root: state.workspaceRoot || "",
        active_project: state.currentProject || null,
        active_session: state.activeSession,
        sessions: state.sessions.map((s) => ({
          id: s.id, name: s.name,
          messages: s.messages.slice(-60),
          attachments: [],
          ts: s.ts,
          pinned: !!s.pinned, pinnedAt: s.pinnedAt || null,
          archived: !!s.archived, archivedAt: s.archivedAt || null,
          project: s.project || null,
        })),
        hidden_projects: state.hiddenProjects || [],
        workspaces: state.wsHistory || {},  // 各工作区根目录的会话/配置快照
        theme: document.body.dataset.theme || "dark",
        paper_style: state.paperStyle || { ...PAPER_STYLE_DEFAULTS },
        pane_widths: {
          left: document.body.classList.contains("left-collapsed")
            ? (state._leftW || PANE_LIMITS.left.def) : paneWidth("left"),
          preview: paneWidth("preview"),
          chat: paneWidth("chat"),
          right: paneWidth("right"),
        },
        preview_open: document.querySelector("#previewPane")?.classList.contains("open") || false,
        total_words: $("#totalWords")?.value || null,
        template: $("#templateSelect")?.value || null,
        domain: $("#domainSelect")?.value || null,
        reference_paper: $("#refSelect")?.value || null,
        chat_open: true,
      }),
      keepalive: !!keepalive,
    });
    if (resp.status === 409) {
      // 版本落后（多为残留的未刷新标签页持有旧状态）
      const fresh = await api("/api/workspace").catch(() => null);
      if (!fresh) return;
      state.wsRev = (fresh.state && fresh.state.rev) || 0;
      if ((fresh.workspace_root || "") !== (state.workspaceRoot || "")) {
        _markStaleTab();  // 本页是旧工作区的残留视图：回到前台时刷新
        return;
      }
      if (!_wsSaveRetry) {  // 同根并发保存撞版本号：对齐后重试一次
        _wsSaveRetry = true;
        try { await saveWorkspaceNow(keepalive); } finally { _wsSaveRetry = false; }
      }
      return;
    }
    if (resp.ok) {
      const r = await resp.json().catch(() => ({}));
      if (r && r.rev) state.wsRev = r.rev;
    }
  } catch (e) { /* 静默：保存失败不打断使用 */ }
}

/* ================= 工作区切换（v25：按根目录完全隔离） =================
   会话/当前项目/论文配置随工作区根目录隔离：切换时把旧工作区快照存入
   state.wsHistory（随工作区状态落盘），新根有快照则恢复、没有则全新开始。
   顶层 sessions 字段始终等于「当前根」的会话（后端对话上下文随 PUT 重建）。 */

/* 根目录快照键：统一斜杠 + 去尾斜杠 + 小写（Windows 大小写不敏感） */
function wsKey(root) {
  return String(root || "").trim().replace(/[\\/]+$/, "").replace(/\\/g, "/").toLowerCase();
}

/* 项目是否位于当前工作区根目录下（全量隔离：其他根的项目不显示、不自动恢复） */
function projectInRoot(p) {
  const rootKey = wsKey(state.workspaceRoot);
  if (!rootKey) return true;
  return wsKey((p && p.path) || "").startsWith(rootKey + "/");
}

/* 把当前工作区（root）的会话与配置快照进 wsHistory */
function snapshotCurrentWorkspace(root) {
  if (!root) return;
  const key = wsKey(root);
  // 防污染保护：已有快照比当前顶层状态更"丰富"（消息更多）时保留旧快照——
  // 避免顶层被并发保存污染成空会话后，切回时反把好快照覆盖掉
  const existing = state.wsHistory[key];
  const curMsgs = state.sessions.reduce((a, s) => a + (s.messages || []).length, 0);
  const oldMsgs = (existing && existing.sessions || []).reduce((a, s) => a + (s.messages || []).length, 0);
  if (existing && oldMsgs > curMsgs
      && (existing.active_project || oldMsgs > 0)) {
    existing.ts = Date.now();  // 只刷新时间防淘汰，内容保留
    return;
  }
  state.wsHistory[key] = {
    ts: Date.now(),
        sessions: state.sessions.map((s) => ({
          id: s.id, name: s.name,
          messages: s.messages.slice(-60),
          attachments: [],
          ts: s.ts,
          pinned: !!s.pinned, pinnedAt: s.pinnedAt || null,
          archived: !!s.archived, archivedAt: s.archivedAt || null,
          project: s.project || null,
        })),
    active_session: state.activeSession,
    active_project: state.currentProject || null,
    total_words: $("#totalWords")?.value || null,
    template: $("#templateSelect")?.value || null,
    domain: $("#domainSelect")?.value || null,
    reference_paper: $("#refSelect")?.value || null,
    hidden_projects: (state.hiddenProjects || []).slice(),
  };
  pruneWsHistory();
}

/* 快照数量上限：最多保留 8 个工作区，超出按最后使用时间淘汰最旧的 */
function pruneWsHistory(max = 8) {
  const keys = Object.keys(state.wsHistory);
  if (keys.length <= max) return;
  keys.sort((a, b) => (state.wsHistory[a].ts || 0) - (state.wsHistory[b].ts || 0));
  keys.slice(0, keys.length - max).forEach((k) => delete state.wsHistory[k]);
}

/* 应用某工作区的快照（snap 为空 = 全新工作区：默认配置 + 一个空会话） */
function applyWorkspaceSnapshot(snap) {
  state.sessions = [];
  state.activeSession = -1;
  if (snap && Array.isArray(snap.sessions) && snap.sessions.length) {
    state.sessions = snap.sessions.map((s, i) => ({
      id: s.id || `chat_${Date.now().toString(36)}_${i}`,
      name: s.name || `会话 ${i + 1}`,
      messages: Array.isArray(s.messages) ? s.messages : [],
      attachments: [],
      pendingSelections: [],
      ts: s.ts || (Date.now() - i * 60e3),
      pinned: !!s.pinned, pinnedAt: s.pinnedAt || undefined,
      archived: !!s.archived, archivedAt: s.archivedAt || undefined,
      project: typeof s.project === "string" ? s.project : null,
    }));
    state.activeSession = Math.min(Number(snap.active_session) || 0, state.sessions.length - 1);
  } else {
    addSession();  // 全新工作区：一个空会话
  }
  // 论文配置随工作区走：无快照 = 回默认值
  $("#totalWords").value = snap?.total_words || 30000;
  state.template = snap?.template || "chinese_journal";
  try { $("#templateSelect").value = state.template; } catch (e) { /* ignore */ }
  state.domain = snap?.domain || "auto";
  try { $("#domainSelect").value = state.domain; } catch (e) { /* ignore */ }
  $("#refSelect").value = snap?.reference_paper || "";
  syncRefSelectTitle();
  loadTemplatePanel();  // 无参考论文时收起模板档案卡
  state.hiddenProjects = Array.isArray(snap?.hidden_projects)
    ? snap.hidden_projects.filter((n) => typeof n === "string") : [];
}

async function applyWorkspaceRoot(root) {
  const oldRoot = state.workspaceRoot;
  let resp;
  try {
    resp = await api("/api/workspace/root", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root }),
    });
  } catch (e) {
    toast(`工作区设置失败: ${e.message}`, "err");
    return;
  }
  // 1) 旧工作区快照（切回时可恢复）
  snapshotCurrentWorkspace(oldRoot);
  // 2) 切换到新根：清掉旧工作区的一切运行时痕迹
  state.workspaceRoot = resp.workspace_root;
  state.wsRev = resp.rev || state.wsRev;  // 切根推进了版本号，随后的保存须用新值
  state.currentProject = null;
  state.outline = null;
  state.projectUnderstood = false;
  state.aiChecks = {};
  state.editingSid = null;
  closeFilePreview();
  $("#reviewSlot").innerHTML = "";
  $("#fileTree").innerHTML = '<div class="empty-hint">先选择一个项目</div>';
  // 3) 恢复新根的快照（首次使用该根 = 全新工作区）
  const snap = state.wsHistory[wsKey(state.workspaceRoot)];
  applyWorkspaceSnapshot(snap);
  renderSessionBar();
  renderChat();
  renderOutline();
  renderPaperView();
  const rootEl = $("#wsRootPath");
  if (rootEl) { rootEl.textContent = `workspace: ${state.workspaceRoot}`; rootEl.title = state.workspaceRoot; }
  await loadProjects();
  // 4) 恢复该工作区上次选中的项目——必须仍存在且位于当前根目录下（防快照污染带回他区项目）
  if (snap?.active_project) {
    const known = (state.projects.registered || []).find((r) => r.name === snap.active_project)
      || (state.projects.candidates || []).find((c) => c.name === snap.active_project);
    if (known && projectInRoot(known)) await selectProject(snap.active_project, known.path, true, true);
  }
  renderChat();  // 引导卡的当前项目文案随切换刷新（selectProject 不重绘聊天区）
  scheduleSave();
  toast(snap
    ? `已切换工作区：${resp.workspace_root}（恢复 ${state.sessions.length} 个会话）`
    : `已切换到新工作区：${resp.workspace_root}`, "ok");
  closeFolderPicker();
  closePaneView();        // 侧栏回到（新工作区的）会话列表
  switchWsTab("paper");   // 主区切到论文页：向导或该项目论文
}

function setWorkspaceRoot() { openFolderPicker(); }

function openFolderPicker() {
  $("#fpOverlay").classList.add("open");
  state.fpPath = state.workspaceRoot || "";
  $("#fpPathInput").value = state.workspaceRoot || "";
  fpBrowse(state.workspaceRoot || "");
}
function closeFolderPicker() { $("#fpOverlay").classList.remove("open"); }

let _fpSeq = 0;  // 浏览请求序号：快速连点/重开时丢弃过期响应，防旧结果覆盖新结果
async function fpBrowse(path) {
  const seq = ++_fpSeq;
  const list = $("#fpList");
  list.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
  try {
    const d = await api("/api/fs/list?path=" + encodeURIComponent(path || ""));
    if (seq !== _fpSeq) return;  // 已有更新的浏览请求，丢弃本次过期响应
    state.fpPath = d.path || "";
    state.fpParent = d.parent || "";
    $("#fpPathInput").value = d.path || "";
    $("#fpUp").disabled = !!d.is_root;  // 磁盘根目录的上级 = 盘符列表
    $("#fpChoose").disabled = d.is_root || !d.path;  // 盘符层不可选，须进入具体目录
    $("#fpHint").textContent = d.is_root ? "请进入某个磁盘后选择目录"
      : (d.path === state.workspaceRoot ? "当前已是工作区（可重新选择）" : `将选择：${d.path}`);
    list.innerHTML = (d.dirs || []).map((x) => `
      <div class="fp-row" data-p="${esc(x.path)}">
        <span class="fp-ico">📁</span><span class="fp-name">${esc(x.name)}</span>
        <span class="fp-go">›</span>
      </div>`).join("") || '<div class="empty-hint">无子目录</div>';
    list.querySelectorAll(".fp-row").forEach((r) => {
      r.addEventListener("click", () => fpBrowse(r.dataset.p));
      r.addEventListener("dblclick", () => applyWorkspaceRoot(r.dataset.p));  // 双击直接选定
    });
  } catch (e) {
    if (seq !== _fpSeq) return;
    const msg = /fetch/i.test(e.message)
      ? "服务未连接：请确认论文智能体服务已启动（或刷新页面后重试）"
      : e.message;
    list.innerHTML = `<div class="fp-err">⚠ ${esc(msg)}<br>
      <button class="btn-mini" id="fpRetry">↻ 重试</button></div>`;
    $("#fpRetry")?.addEventListener("click", () => fpBrowse(path));
  }
}

function bindFolderPicker() {
  $("#fpClose").addEventListener("click", closeFolderPicker);
  $("#fpCancel").addEventListener("click", closeFolderPicker);
  $("#fpOverlay").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeFolderPicker();
  });
  $("#fpUp").addEventListener("click", () => fpBrowse(state.fpParent || ""));
  $("#fpGo").addEventListener("click", () => fpBrowse($("#fpPathInput").value.trim()));
  $("#fpPathInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") fpBrowse(e.target.value.trim());
  });
  $("#fpChoose").addEventListener("click", () => {
    const root = ($("#fpPathInput").value || "").trim() || state.fpPath;
    if (!root) { $("#fpHint").textContent = "请先进入某个目录"; return; }
    applyWorkspaceRoot(root);
  });
}

async function refreshSystemInfo() {
  try {
    const info = await api("/api/info");
    const m = info.models || {};
    const mainOk = m.main_llm_available;
    const kb = info.kb || {};
    const writeCount = kb.writing_methodology?.document_count ?? 0;
    const methodCount = kb.method_papers?.document_count ?? 0;
    $("#kbWriting").textContent = `${writeCount} 块`;
    $("#kbMethods").textContent = `${methodCount} 块`;
    $("#statusText").innerHTML =
      `主模型 ${mainOk ? '<span class="ok">✓</span>' : '<span class="bad">未配置API</span>'} · ` +
      `嵌入 ${m.embedding_model || "?"}`;
    const chip = $("#composerModel");
    if (chip) chip.textContent = m.main_llm || "GLM";
    if (!mainOk) toast(`主模型「${m.main_llm || "?"}」未就绪：请在设置页填写对应 API Key（智谱 / DeepSeek）`, "err");
  } catch (e) {
    $("#statusText").innerHTML = '<span class="bad">服务未连接</span>';
  }
}

async function loadTemplates() {
  // 论文结构模板 + 论文领域（均可配置；领域决定写作人设/语言规范/自查清单）
  try {
    state.templates = await api("/api/outline/templates");
    const tSel = $("#templateSelect");
    if (tSel) {
      const prev = tSel.value || state.template || "chinese_journal";
      tSel.innerHTML = Object.entries(state.templates)
        .map(([k, t]) => `<option value="${esc(k)}">${esc(t.name || k)}</option>`).join("");
      tSel.value = prev in state.templates ? prev : "chinese_journal";
      tSel.onchange = () => { state.template = tSel.value; scheduleSave(); };
      state.template = tSel.value;
    }
  } catch (e) { /* ignore */ }
  try {
    const resp = await api("/api/domains");
    const dSel = $("#domainSelect");
    if (dSel && Array.isArray(resp.domains)) {
      const prev = dSel.value || state.domain || "auto";
      dSel.innerHTML = resp.domains
        .map((d) => `<option value="${esc(d.key)}">${esc(d.label)}</option>`).join("");
      dSel.value = resp.domains.some((d) => d.key === prev) ? prev : "auto";
      dSel.onchange = () => { state.domain = dSel.value; scheduleSave(); };
      state.domain = dSel.value;
    }
  } catch (e) { /* ignore */ }
}

/* ================= 项目 ================= */
async function loadProjects() {
  try {
    const q = state.workspaceRoot ? `?root=${encodeURIComponent(state.workspaceRoot)}` : "";
    state.projects = await api("/api/projects" + q);
    state.projects._loaded = true;
    renderProjects();
  } catch (e) { toast(`项目列表加载失败: ${e.message}`, "err"); }
}

/* ---------- 项目列表过滤（纯前端显示，后端注册数据/画像/大纲不受影响） ----------
   FRONTEND_HIDDEN_PROJECTS：硬编码永久不显示的项目条目（无视工作区状态）；
   state.hiddenProjects：用户右键手动隐藏的（右键菜单可恢复），随工作区状态持久化 */
const FRONTEND_HIDDEN_PROJECTS = ["论文智能体"];
function hiddenProjects() {
  return state.hiddenProjects || [];
}
function setHiddenProjects(list) {
  state.hiddenProjects = list || [];
  scheduleSave();
}

function renderProjects() {
  const box = $("#projectList");
  const userHidden = new Set(hiddenProjects());
  const hidden = new Set([...FRONTEND_HIDDEN_PROJECTS, ...userHidden]);
  // 已注册项目是全局登记的：只显示位于当前工作区根目录下的（其他根的随工作区切换隐藏）
  const registered = (state.projects.registered || []).filter((p) => !hidden.has(p.name) && projectInRoot(p));
  const candidates = (state.projects.candidates || []).filter((c) => !hidden.has(c.name));
  if (!registered.length && !candidates.length) {
    box.innerHTML = '<div class="empty-hint">未发现项目目录</div>';
  } else {
    let html = "";
    for (const p of registered) {
      const sel = state.currentProject === p.name ? "selected" : "";
      html += `<div class="project-item ${sel}" data-name="${esc(p.name)}" data-path="${esc(p.path)}" data-registered="1">
        <span>📁</span><span class="name">${esc(p.name)}</span>
        <span class="badge outline">${p.has_outline ? "有大纲" : `${p.methods_count || 0}方法`}</span>
      </div>`;
    }
    for (const c of candidates) {
      if (registered.some((r) => r.path === c.path)) continue;
      const sel = state.currentProject === c.name ? "selected" : "";
      html += `<div class="project-item ${sel}" data-name="${esc(c.name)}" data-path="${esc(c.path)}" data-registered="">
        <span>📂</span><span class="name">${esc(c.name)}</span>
        <span class="badge">${c.python_scripts}脚本</span>
      </div>`;
    }
    box.innerHTML = html;
    box.querySelectorAll(".project-item").forEach((el) => {
      el.addEventListener("click", () => selectProject(el.dataset.name, el.dataset.path, !!el.dataset.registered));
      el.addEventListener("contextmenu", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        openProjectCtxMenu(ev.clientX, ev.clientY, el.dataset.name);
      });
    });
  }
}

function openProjectCtxMenu(x, y, name) {
  $("#projectCtxMenu")?.remove();
  if (!name) return;
  const menu = document.createElement("div");
  menu.className = "context-menu show";
  menu.id = "projectCtxMenu";
  const userHidden = hiddenProjects();
  menu.innerHTML =
    `<div class="item" data-act="hide">🚫 从列表隐藏</div>`
    + (userHidden.length
      ? `<div class="item" data-act="restore">👁 恢复全部隐藏项目（${userHidden.length}）</div>`
      : "")
    + `<div class="sep"></div>`
    + `<div class="item" data-act="hint" style="color:var(--text-faint);font-size:11.5px;cursor:default;">仅前端隐藏 · 画像/大纲保留</div>`;
  document.body.appendChild(menu);
  menu.style.left = `${Math.max(6, Math.min(x, window.innerWidth - 230))}px`;
  menu.style.top = `${Math.max(6, Math.min(y, window.innerHeight - menu.offsetHeight - 10))}px`;
  menu.addEventListener("click", (ev) => {
    const item = ev.target.closest("[data-act]");
    if (!item || item.dataset.act === "hint") return;
    menu.remove();
    if (item.dataset.act === "hide") {
      const list = hiddenProjects();
      if (!list.includes(name)) list.push(name);
      setHiddenProjects(list);
      toast(`「${name}」已从列表隐藏（后端数据保留，右键任意项目可恢复）`, "ok");
      renderProjects();
    } else if (item.dataset.act === "restore") {
      setHiddenProjects([]);
      renderProjects();
      toast("已恢复全部项目", "ok");
    }
  });
}

async function selectProject(name, path, isRegistered, silent = false) {
  state.currentProject = name;
  renderProjects();
  renderSessionBar();  // 面包屑随项目切换即时更新
  if (!silent) {
    // 任务一致性（用户主动点选项目 = 切换任务）：切到该项目最近的未归档会话；
    // 该项目还没有会话时，把当前未绑定的空会话收编过去（旧数据平滑迁移），
    // 否则为它新开一个专属会话（一个项目对应 n 个会话的模型）
    const cand = state.sessions.map((s, i) => ({ s, i }))
      .filter(({ s }) => !s.archived && s.project === name)
      .sort((a, b) => (b.s.ts || 0) - (a.s.ts || 0))[0];
    if (cand) {
      if (cand.i !== state.activeSession) {
        state.activeSession = cand.i;
        renderChat();
      }
    } else {
      const cur = activeSession();
      if (cur && !cur.project) {
        cur.project = name;
        renderSessionBar();
      } else {
        addSession(name);
      }
    }
  }
  if (!isRegistered) {
    if (!silent) toast(`正在注册并分析项目「${name}」…`);
    try {
      await api("/api/projects/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name, path,
          domain: ($("#domainSelect")?.value === "auto") ? null : ($("#domainSelect")?.value || null),
        }),
      });
      if (!silent) toast(`项目「${name}」画像构建完成`, "ok");
      await loadProjects();
      // 选区后第一任务：项目理解向导（理清顺序/核心脚本/结果图表，弹窗澄清模糊处）
      openUnderstandWizard(name, path, true).catch((e) => console.warn("理解向导启动失败", e));
    } catch (e) {
      toast(`项目注册失败: ${e.message}`, "err");
      return;
    }
  }
  loadTree(name);
  await tryLoadOutline(name);
  renderOutline();
  loadTemplatePanel();  // 项目切换：刷新「一键套入」状态（套入是项目级设置）
  // 项目理解状态（准备向导步骤③）：查《研究项目主体》是否已存在
  try {
    const b = await api(`/api/project/body?project_name=${encodeURIComponent(name)}`);
    state.projectUnderstood = !!(b.exists && b.body);
  } catch (e) { state.projectUnderstood = false; }
  renderPaperView();
  if (!silent) {
    const hasOutline = !!state.outline;
    toast(hasOutline ? `已加载「${name}」及其现有大纲` : `已选择项目「${name}」`, "ok");
  }
  scheduleSave();
}

async function loadTree(name) {
  const box = $("#fileTree");
  box.innerHTML = '<div class="empty-hint">加载文件树…</div>';
  try {
    const tree = await api(`/api/projects/${encodeURIComponent(name)}/tree?max_depth=4`);
    box.innerHTML = "";
    box.appendChild(renderTreeNode(tree));
  } catch (e) {
    box.innerHTML = `<div class="empty-hint">文件树加载失败: ${esc(e.message)}</div>`;
  }
}

function renderTreeNode(node) {
  const wrap = document.createElement("div");
  wrap.className = "tree-node";
  const row = document.createElement("div");
  row.className = "tree-row";
  const isDir = node.type === "dir";
  const img = node.is_image || (!isDir && isImageFile(node.name));
  row.innerHTML = `<span class="icon">${isDir ? "📁" : iconForFile(node.name)}</span><span class="label">${esc(node.name)}</span>`;
  row.dataset.path = node.path;
  row.draggable = true;
  row.title = node.path;
  row.addEventListener("click", () => {
    wrap.querySelectorAll(":scope > .tree-children").forEach((c) => c.remove());
    if (isDir && Array.isArray(node.children)) {
      const children = document.createElement("div");
      children.className = "tree-children";
      node.children.forEach((ch) => children.appendChild(renderTreeNode(ch)));
      wrap.appendChild(children);
    } else if (!isDir) {
      document.querySelectorAll(".tree-row.selected").forEach((r) => r.classList.remove("selected"));
      row.classList.add("selected");
    }
  });
  row.addEventListener("dragstart", (ev) => {
    ev.dataTransfer.setData("text/plain", JSON.stringify(
      isDir ? { isDir: 1, path: node.path, name: node.name }
            : { path: node.path, name: node.name }));
    ev.dataTransfer.effectAllowed = "copy";
  });
  row.addEventListener("contextmenu", (ev) => {
    ev.preventDefault();
    showContextMenu(ev.clientX, ev.clientY, node);
  });
  if (!isDir) {
    // 双击直接预览（文本→会话内；图片→弹窗）
    row.addEventListener("dblclick", () => previewFile(node));
  }
  wrap.appendChild(row);
  return wrap;
}

const IMAGE_EXTS = ["png", "jpg", "jpeg", "gif", "svg", "bmp", "webp", "tif", "tiff", "ico"];

function extOf(name) {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
}
function isImageFile(name) { return IMAGE_EXTS.includes(extOf(name)); }

function iconForFile(name) {
  const e = extOf(name);
  if (IMAGE_EXTS.includes(e)) return "🖼️";
  if (e === "py") return "🐍";
  if (e === "md") return "📘";
  if (e === "json" || e === "yaml" || e === "yml") return "⚙️";
  if (e === "pdf") return "📕";
  if (e === "csv" || e === "xlsx" || e === "xls") return "📊";
  if (e === "gmt" || e === "tsv") return "🧬";
  if (e === "r" || e === "R") return "📈";
  if (e === "log" || e === "txt") return "📃";
  return "📄";
}

/* ================= 右键菜单 ================= */
let ctxTargetFile = null;
function showContextMenu(x, y, node) {
  ctxTargetFile = node;
  const menu = $("#contextMenu");
  const isDir = node.type === "dir";
  $("#ctxAddDir").style.display = isDir ? "flex" : "none";
  $("#ctxAddToSession").style.display = isDir ? "none" : "flex";
  const img = !isDir && isImageFile(node.name);
  $("#ctxPreviewImage").style.display = img ? "flex" : "none";
  $("#ctxPreviewFile").style.display = (!isDir && !img) ? "flex" : "none";
  menu.classList.remove("show");
  void menu.offsetWidth; // 重置动画
  menu.classList.add("show");
  menu.style.left = `${Math.min(x, window.innerWidth - 190)}px`;
  menu.style.top = `${Math.min(y, window.innerHeight - 120)}px`;
}
document.addEventListener("click", () => { $("#contextMenu").classList.remove("show"); });
document.addEventListener("click", (ev) => {
  // 会话/项目右键菜单：点外部即关（菜单自身的 item click 已先处理并 remove）
  if (ev.target.closest("#sessionCtxMenu")) return;
  document.getElementById("sessionCtxMenu")?.remove();
  if (ev.target.closest("#projectCtxMenu")) return;
  document.getElementById("projectCtxMenu")?.remove();
});
window.addEventListener("resize", () => {
  document.getElementById("sessionCtxMenu")?.remove();
  document.getElementById("projectCtxMenu")?.remove();
}, { passive: true });
$("#ctxAddToSession").addEventListener("click", () => {
  if (ctxTargetFile) addAttachment(ctxTargetFile.path, ctxTargetFile.name);
});
$("#ctxAddDir").addEventListener("click", () => {
  if (ctxTargetFile) collectAndInsertDir(ctxTargetFile.path, ctxTargetFile.name);
});
$("#pvClose").addEventListener("click", closeFilePreview);
$("#ctxPreviewFile").addEventListener("click", () => {
  if (ctxTargetFile) previewFile(ctxTargetFile);
});
$("#ctxPreviewImage").addEventListener("click", () => {
  if (ctxTargetFile) openImageModal(ctxTargetFile);
});

/* ================= VS Code 式文件预览面板 ================= */
const HL_LANG = { py: "python", python: "python", md: "markdown", json: "json",
  yaml: "yaml", yml: "yaml", r: "r", js: "javascript", ts: "typescript",
  html: "html", css: "css", xml: "xml", sh: "bash", bat: "bat", toml: "toml",
  csv: "plaintext", tsv: "plaintext", txt: "plaintext", log: "plaintext", gmt: "plaintext" };
window._lastPreviewPath = null;
window._lastPreviewName = null;

function openFilePreview(path, name, silent) {
  const pane = document.querySelector("#previewPane");
  pane.style.display = "flex";
  pane.classList.add("open");
  applyPaneWidth("preview", paneWidth("preview"));
  window._lastPreviewPath = path;
  window._lastPreviewName = name;
  document.querySelector("#pvName").textContent = name;
  document.querySelector("#pvIcon").textContent = iconForFile(name);
  const segs = path.split(/[\\/]/).filter(Boolean);
  document.querySelector("#pvBreadcrumb").innerHTML =
    segs.map((s) => `<span>${esc(s)}</span>`).join('<span class="crumb-sep">›</span>');
  const notice = document.querySelector("#pvNotice");
  const codeBody = document.querySelector("#pvCodeBody");
  const gutter = document.querySelector("#pvGutter");
  codeBody.innerHTML = [92, 76, 84, 60, 88, 45].map((w) =>
    `<div class="skeleton" style="height:11px;margin:7px 0;width:${w}%;"></div>`).join("");
  gutter.textContent = "";
  notice.style.display = "none";
  (async () => {
    try {
      const info = await api(`/api/file?path=${encodeURIComponent(path)}&max_chars=800000`);
      if (info.type === "binary") {
        codeBody.textContent = "二进制文件，无法预览。";
        return;
      }
      let code = info.content || "";
      let lines = code.split("\n");
      const total = lines.length;
      let cut = false;
      if (total > 5000) { lines = lines.slice(0, 5000); cut = true; }
      code = lines.join("\n");
      gutter.textContent = lines.map((_, i) => i + 1).join("\n");
      const ext = (name.split(".").pop() || "").toLowerCase();
      const lang = HL_LANG[ext] || "plaintext";
      if (window.hljs && code.length < 300000) {
        try {
          codeBody.innerHTML = hljs.highlight(code, { language: lang }).value;
        } catch (e) { codeBody.textContent = code; }
      } else {
        codeBody.textContent = code;
      }
      if (cut || info.truncated) {
        notice.textContent = `⚠ 文件过大，仅显示前 ${lines.length} 行（完整文件共 ${total} 行）。`;
        notice.style.display = "block";
      }
    } catch (e) {
      codeBody.textContent = "加载失败: " + e.message;
    }
  })();
  scheduleSave();
}

function closeFilePreview() {
  const pane = document.querySelector("#previewPane");
  pane.classList.remove("open");
  pane.style.display = "none";
  window._lastPreviewPath = null;
  scheduleSave();
}

/* 文件预览分发：图片→弹窗，其余→预览面板 */
function previewFile(node) {
  if (isImageFile(node.name)) { openImageModal(node); return; }
  openFilePreview(node.path, node.name);
}

/* 图片预览弹窗 */
function openImageModal(node) {
  const url = rawUrl(node.path);
  $("#imgModalImg").src = url;
  $("#imgModalTitle").textContent = `🖼 ${node.name}`;
  $("#imgModalOpen").onclick = () => window.open(url, "_blank");
  $("#imgModal").classList.add("open");
}
$("#imgModalClose").addEventListener("click", () => {
  $("#imgModal").classList.remove("open");
  $("#imgModalImg").src = "";
});
$("#imgModal").addEventListener("click", (ev) => {
  if (ev.target === $("#imgModal")) {
    $("#imgModal").classList.remove("open");
    $("#imgModalImg").src = "";
  }
});

/* ================= 会话 ================= */
function activeSession() { return state.sessions[state.activeSession]; }

/* ================= ZCode 式布局：侧栏/抽屉/Tab（v18） ================= */
function relTime(ts) {
  if (!ts) return "";
  const diff = Date.now() - Number(ts);
  if (diff < 60e3) return "刚刚";
  if (diff < 3600e3) return Math.floor(diff / 60e3) + " 分钟前";
  if (diff < 86400e3) return Math.floor(diff / 3600e3) + " 小时前";
  if (diff < 7 * 86400e3) return Math.floor(diff / 86400e3) + " 天前";
  return new Date(Number(ts)).toLocaleDateString("zh-CN");
}
/* ================= 侧栏复用面板：会话列表 / 项目与文件 / 知识库 =================
   点侧栏「项目与文件」「知识库」时，在侧栏会话列表区域原地切换显示，
   再点一次或 ✕ / Esc 回到会话列表。主区对话不受影响。 */
let _paneView = "chat";
function paneView() { return _paneView; }
function openPaneView(name) {
  if (_paneView === name) { closePaneView(); return; }  // 再点同一切回会话
  _paneView = name;
  const sb = document.querySelector(".sidebar");
  if (sb) sb.dataset.pane = name;
  $("#sbPaneProjects").hidden = name !== "projects";
  $("#sbPaneKb").hidden = name !== "kb";
  $("#sbDrawerProjects")?.classList.toggle("active", name === "projects");
  $("#sbDrawerKb")?.classList.toggle("active", name === "kb");
  if (name === "projects" && !state.projects._loaded) {
    state.projects._loaded = true;
    loadProjects();
  }
}
function closePaneView() {
  _paneView = "chat";
  const sb = document.querySelector(".sidebar");
  if (sb) delete sb.dataset.pane;
  $("#sbPaneProjects").hidden = true;
  $("#sbPaneKb").hidden = true;
  $("#sbDrawerProjects")?.classList.remove("active");
  $("#sbDrawerKb")?.classList.remove("active");
}
function bindShellExtras() {
  bindFolderPicker();
  bindDualSplit();
  // 论文配置滑出面板：⚙ 打开 / ✕ 或遮罩关闭
  $("#btnCfg")?.addEventListener("click", openCfgPanel);
  $("#cfgClose")?.addEventListener("click", closeCfgPanel);
  $("#cfgScrim")?.addEventListener("click", closeCfgPanel);
  $("#sbNewChat")?.addEventListener("click", addSession);
  $("#sbDrawerProjects")?.addEventListener("click", () => openPaneView("projects"));
  $("#sbDrawerKb")?.addEventListener("click", () => openPaneView("kb"));
  document.querySelectorAll("[data-cp-close]").forEach((b) =>
    b.addEventListener("click", closePaneView));
  $("#ftTopBadge")?.addEventListener("click", () => {
    switchWsTab("paper");
    const strip = $("#fulltextStrip");
    if (strip && strip.style.display !== "none") strip.scrollIntoView({ behavior: "smooth", block: "center" });
  });
  $("#sbSearch")?.addEventListener("input", (e) => {
    state.sbQuery = e.target.value.trim().toLowerCase();
    renderSessionBar();
  });
  document.querySelectorAll("#sbViewTabs [data-sbview]").forEach((b) =>
    b.addEventListener("click", () => {
      state.sbView = b.dataset.sbview;
      renderSessionBar();
    }));
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "n") {
      e.preventDefault();
      addSession();
    }
  });
  bindEscLayers();
  bindMviewSwitch();
}

/* ================= 统一 Esc 弹层栈（ui.js）
   注册顺序 = 关闭优先级：后注册的先被 Esc 关闭。
   isOpen 闭包实时查询 DOM，动态创建的弹窗（理解向导/AI检测等）无需单独注册。 */
function bindEscLayers() {
  const byId = (id, cls = "open", after) => ({
    isOpen: () => {
      const el = document.getElementById(id);
      return !!el && el.classList.contains(cls);
    },
    close: () => {
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.remove(cls);
      if (after) after(el);
    },
  });
  const menuShow = (id) => byId(id, "show");
  // 低优先级 → 高优先级
  // 会话区视图（项目与文件/知识库）：非对话视图时 Esc 先回对话
  ui.layer("paneView", () => paneView() !== "chat", closePaneView);
  // 认证遮罩（authOverlay）不注册：登录态必须显式操作，不允许 Esc 关闭
  ui.layer("settings", byId("settingsPage"));
  ui.layer("admin", byId("adminModal"));
  ui.layer("folderPicker", byId("fpOverlay"));
  ui.layer("cfg", () => $("#taskPanel")?.classList.contains("open"), closeCfgPanel);
  ui.layer("outlineModal", byId("outlineModal"));
  ui.layer("understand", byId("understandWizard", "open", closeUwModal));
  ui.layer("aicenter", byId("aiCenterModal"));
  ui.layer("tplModal",
    () => !!document.getElementById("tplModal"),
    () => document.getElementById("tplModal")?.remove());
  ui.layer("figPicker", byId("figPicker"));
  ui.layer("figWorkshop", byId("figWorkshop"));
  ui.layer("imgModal", byId("imgModal", "open", () => { $("#imgModalImg").src = ""; }));
  ui.layer("ctxMenu", menuShow("contextMenu"));
  ui.layer("sessionCtxMenu",
    () => !!document.getElementById("sessionCtxMenu"),
    () => document.getElementById("sessionCtxMenu")?.remove());
  ui.layer("projectCtxMenu",
    () => !!document.getElementById("projectCtxMenu"),
    () => document.getElementById("projectCtxMenu")?.remove());
  ui.layer("strengthMenu", menuShow("strengthMenu"));
  ui.layer("styleMenu", menuShow("styleMenu"));
  ui.layer("fullMenu", menuShow("fullMenu"));
}

/* 窄屏单栏模式（<960px）：[对话|论文] 顶栏切换 + 汉堡键唤出侧栏（遮罩由 CSS 控制） */
function bindMviewSwitch() {
  document.body.dataset.mview = "chat";
  document.querySelectorAll(".mview-switch [data-mview]").forEach((b) =>
    b.addEventListener("click", () => {
      document.body.dataset.mview = b.dataset.mview;
      document.querySelectorAll(".mview-switch [data-mview]").forEach((x) =>
        x.classList.toggle("active", x === b));
    }));
  $("#btnSbHamburger")?.addEventListener("click", () =>
    document.body.classList.toggle("sb-open"));
  $("#sbScrim")?.addEventListener("click", () => document.body.classList.remove("sb-open"));
}
/* 双窗分隔条：拖动调整对话/论文宽度（比例持久化，双击复位）；
   ≤960px 单栏模式下清掉内联宽度，交还媒体查询接管 */
function bindDualSplit() {
  const split = $("#dualSplit"), chat = $("#chatPane");
  if (!split || !chat) return;
  const KEY = "moji.dualChatPct";
  const narrow = () => window.innerWidth <= 960;
  const apply = (pct) => {
    if (narrow()) { chat.style.flex = ""; return; }
    const v = Math.min(72, Math.max(28, pct));
    chat.style.flex = `0 0 ${v}%`;
    try { localStorage.setItem(KEY, String(v)); } catch (_) {}
  };
  try {
    const saved = parseFloat(localStorage.getItem(KEY));
    if (!isNaN(saved)) apply(saved);
  } catch (_) {}
  window.addEventListener("resize", () => {
    if (narrow()) chat.style.flex = "";
  }, { passive: true });
  split.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    split.setPointerCapture(e.pointerId);
    const row = split.parentElement.getBoundingClientRect();
    const move = (ev) =>
      apply(((ev.clientX - row.left) / row.width) * 100);
    const up = () => {
      split.removeEventListener("pointermove", move);
      split.removeEventListener("pointerup", up);
    };
    split.addEventListener("pointermove", move);
    split.addEventListener("pointerup", up);
  });
  split.addEventListener("dblclick", () => apply(46));
}

/* 进度清单（ZCode「进程 0/5」式）：按大纲节状态渲染 */
function renderTaskProgress() {
  const box = $("#taskProgress"), list = $("#taskProgressList");
  if (!box || !list) return;
  const outline = state.outline;
  const secs = (outline && outline.sections) || [];
  if (!secs.length) { box.style.display = "none"; return; }
  const done = secs.filter((s) => s.status === "drafted" && (s.content || "").trim()).length;
  const gen = secs.some((s) => s.status === "generating");
  $("#taskProgressCount").textContent = gen ? `${done}/${secs.length} · 生成中` : `${done}/${secs.length}`;
  list.innerHTML = secs.map((s) => {
    const st = s.status === "drafted" && (s.content || "").trim() ? "done"
      : s.status === "generating" ? "gen" : "todo";
    const ico = st === "done" ? '<span class="tp-ico done">✓</span>'
      : st === "gen" ? '<span class="spinner"></span>'
      : '<span class="tp-ico todo">○</span>';
    const words = st === "done" ? `<span class="tp-words">${s.actual_words || 0}字</span>` : "";
    return `<div class="tp-row ${st}" data-sid="${esc(s.id || "")}">
      ${ico}<span class="tp-name">${esc(s.title || "")}</span>${words}</div>`;
  }).join("");
  box.style.display = "";
}

function addSession(project) {
  const id = `chat_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
  state.sessions.push({ id, name: `会话 ${state.sessions.length + 1}`, messages: [],
    attachments: [], pendingSelections: [], ts: Date.now(),
    project: project !== undefined ? project : (state.currentProject || null) });  // 会话归属项目（任务）
  state.activeSession = state.sessions.length - 1;
  state.sbView = "main";  // 新会话在主列表可见
  renderSessionBar();
  renderChat();
  scheduleSave();
}

/* 选择会话 = 切换任务上下文：会话绑定别的项目时，右侧大纲/论文/文件树同步切过去 */
async function selectSession(i) {
  const s = state.sessions[i];
  if (!s) return;
  state.activeSession = i;
  document.body.classList.remove("sb-open"); // 窄屏单栏：选完会话自动收起侧栏
  renderSessionBar();
  renderChat();
  if (s.project && s.project !== state.currentProject) {
    const known = (state.projects.registered || []).find((r) => r.name === s.project)
      || (state.projects.candidates || []).find((c) => c.name === s.project);
    if (known && projectInRoot(known)) {
      await selectProject(s.project, known.path, true, true);
    } else {
      toast(`会话「${s.name}」所属项目「${s.project}」不在当前工作区（可切换工作区后重试）`, "warn");
    }
  }
  scheduleSave();
}

/* 会话显示排序：置顶优先（新置顶在前），其余按最近活动倒序；
   归档视图只列已归档会话。返回 state.sessions 的下标数组 */
function sessionOrder() {
  const archived = state.sbView === "archived";
  const idx = state.sessions.map((s, i) => ({ s, i }))
    .filter(({ s }) => !!s.archived === archived)
    .sort((a, b) => {
      const pa = a.s.pinned ? (a.s.pinnedAt || 0) : -1;
      const pb = b.s.pinned ? (b.s.pinnedAt || 0) : -1;
      if (pa !== pb) return pb - pa;
      return (b.s.ts || 0) - (a.s.ts || 0);
    });
  return idx.map((x) => x.i);
}

function renderSessionBar() {
  // —— 侧栏会话列表：按所属项目（任务）分组 + 置顶/归档视图 + 搜索 + 右键菜单 ——
  const list = $("#sessionList");
  if (list) {
    const q = (state.sbQuery || "").toLowerCase();
    const archived = state.sbView === "archived";
    document.querySelectorAll("#sbViewTabs [data-sbview]").forEach((b) =>
      b.classList.toggle("active", b.dataset.sbview === state.sbView));
    const nArch = state.sessions.filter((s) => s.archived).length;
    const archCnt = $("#sbArchCount");
    if (archCnt) archCnt.textContent = nArch ? ` ${nArch}` : "";
    const order = sessionOrder()
      .filter((i) => {
        if (!q) return true;
        const s = state.sessions[i];
        return (s.name || "").toLowerCase().includes(q)
          || (s.messages || []).some((m) => (m.content || "").toLowerCase().includes(q));
      });
    // 按会话所属项目分组：当前项目排最前，其余按组内最近活动排序
    const groups = [];
    for (const i of order) {
      const key = state.sessions[i].project || "";
      let g = groups.find((x) => x.key === key);
      if (!g) { g = { key, idxs: [], latest: 0 }; groups.push(g); }
      g.idxs.push(i);
      g.latest = Math.max(g.latest, state.sessions[i].ts || 0);
    }
    groups.sort((a, b) => {
      const aCur = a.key === (state.currentProject || "") ? 1 : 0;
      const bCur = b.key === (state.currentProject || "") ? 1 : 0;
      if (aCur !== bCur) return bCur - aCur;
      return b.latest - a.latest;
    });
    const projKnown = (name) =>
      (state.projects.registered || []).find((r) => r.name === name)
      || (state.projects.candidates || []).find((c) => c.name === name);
    let html = "";
    for (const g of groups) {
      const label = !g.key ? "自由会话（未绑定项目）"
        : (projKnown(g.key) ? g.key : `${g.key} · 不在当前工作区`);
      html += `
      <div class="sb-group-row ${g.key === state.currentProject ? "cur" : ""}" data-proj="${esc(g.key)}">
        <span class="sb-group-ico">${g.key ? "📁" : "💬"}</span>
        <span class="sb-group-name">${esc(label)}</span>
        <span class="sb-group-n">${g.idxs.length} 会话</span>
      </div>`;
      html += g.idxs.map((i) => {
        const s = state.sessions[i];
        return `
        <div class="sb-item ${i === state.activeSession ? "active" : ""} ${s.pinned ? "pinned" : ""}"
             data-i="${i}" title="${esc(s.name)}${s.project ? ` · 所属 ${esc(s.project)}` : ""}（双击重命名 · 右键更多操作）">
          ${s.pinned ? '<span class="sb-pin-flag" title="已置顶">📌</span>' : ""}
          <span class="sb-item-name">${esc(s.name)}</span>
          <span class="sb-item-time">${archived ? "已归档" : relTime(s.ts)}</span>
        </div>`;
      }).join("");
    }
    list.innerHTML = html ||
      `<div class="sb-empty">${q ? "无匹配会话" : archived ? "暂无归档会话" : "暂无会话"}</div>`;

    // 项目分组头：悬停 400ms / 点击 → 打开「项目与文件」面板（文件树沿用原区域与逻辑）
    let _hoverTimer = null;
    list.querySelectorAll(".sb-group-row").forEach((row) => {
      const open = () => { openPaneView("projects"); };
      row.addEventListener("mouseenter", () => {
        row.title = `${row.dataset.proj ? "悬停打开文件树 · " : ""}点击展开项目面板`;
        clearTimeout(_hoverTimer);
        _hoverTimer = setTimeout(() => { if (row.isConnected) open(); }, 400);
      });
      row.addEventListener("mouseleave", () => clearTimeout(_hoverTimer));
      row.addEventListener("click", (ev) => { ev.stopPropagation(); open(); });
    });
    list.querySelectorAll(".sb-item").forEach((el) => {
      const i = Number(el.dataset.i);
      el.addEventListener("click", () => selectSession(i));
      el.addEventListener("dblclick", (ev) => {
        ev.preventDefault();
        startSessionRename(i, el);
      });
      el.addEventListener("contextmenu", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        openSessionCtxMenu(ev.clientX, ev.clientY, i);
      });
    });
  }
  // —— 面包屑：项目 / 会话 ——
  const crumbP = $("#crumbProject"), crumbC = $("#crumbPaper");
  if (crumbP) crumbP.textContent = state.currentProject || "未选择项目";
  if (crumbC) {
    const sess = activeSession();
    crumbC.textContent = sess ? sess.name : "新会话";
  }
}

/* ================= 会话右键菜单：置顶 / 重命名 / 归档 / 删除 ================= */

/* 被隐藏（归档/删除）的会话若是当前会话，自动切到主列表第一个可见会话 */
function _switchActiveAway() {
  const visible = sessionOrder().filter((i) => {
    const s = state.sessions[i];
    return state.sbView === "archived" ? true : !s.archived;
  });
  if (visible.includes(state.activeSession)) return;
  const fallback = state.sessions.findIndex((s) => !s.archived);
  state.activeSession = fallback >= 0 ? fallback : -1;
  renderChat();
}

function sessionTogglePin(i) {
  const s = state.sessions[i];
  if (!s) return;
  s.pinned = !s.pinned;
  s.pinnedAt = s.pinned ? Date.now() : undefined;
  toast(s.pinned ? `「${s.name}」已置顶` : `「${s.name}」已取消置顶`, "ok");
  renderSessionBar(); scheduleSave();
}

function sessionArchive(i) {
  const s = state.sessions[i];
  if (!s) return;
  s.archived = true;
  s.archivedAt = Date.now();
  _switchActiveAway();
  toast(`「${s.name}」已归档（侧栏「归档」页可找回）`, "ok");
  renderSessionBar(); scheduleSave();
}

function sessionUnarchive(i) {
  const s = state.sessions[i];
  if (!s) return;
  s.archived = false;
  s.archivedAt = undefined;
  toast(`「${s.name}」已恢复到会话列表`, "ok");
  renderSessionBar(); scheduleSave();
}

async function sessionDelete(i) {
  const s = state.sessions[i];
  if (!s) return;
  const ok = await ui.confirm({
    title: "删除会话", icon: "🗑",
    message: `删除「${s.name}」？（含 ${s.messages.length} 条消息）`,
    danger: true, confirmText: "删除",
  });
  if (!ok) return;
  state.sessions.splice(i, 1);
  if (!state.sessions.length) { addSession(); return; }
  if (state.activeSession >= state.sessions.length) {
    state.activeSession = state.sessions.length - 1;
  }
  _switchActiveAway();
  renderSessionBar(); scheduleSave();
}

function sessionRename(i) {
  const el = document.querySelector(`#sessionList .sb-item[data-i="${i}"]`);
  if (el) el.scrollIntoView({ block: "nearest" });
  startSessionRename(i, el);
}

function openSessionCtxMenu(x, y, i) {
  $("#sessionCtxMenu")?.remove();   // 换目标右键时重建
  const s = state.sessions[i];
  if (!s) return;
  const archived = state.sbView === "archived";
  const rows = [];
  if (!archived) {
    rows.push(`<div class="item" data-act="pin">${s.pinned ? "📍 取消置顶" : "📌 置顶会话"}</div>`);
  }
  rows.push(`<div class="item" data-act="rename">✏ 重命名会话</div>`);
  rows.push(archived
    ? `<div class="item" data-act="unarch">📤 取消归档</div>`
    : `<div class="item" data-act="arch">🗄 归档会话</div>`);
  rows.push('<div class="sep"></div>');
  rows.push(`<div class="item danger" data-act="del">🗑 删除会话…</div>`);
  const menu = document.createElement("div");
  menu.className = "context-menu show";
  menu.id = "sessionCtxMenu";
  menu.innerHTML = rows.join("");
  document.body.appendChild(menu);
  menu.style.left = `${Math.max(6, Math.min(x, window.innerWidth - 200))}px`;
  menu.style.top = `${Math.max(6, Math.min(y, window.innerHeight - menu.offsetHeight - 10))}px`;
  menu.addEventListener("click", (ev) => {
    const item = ev.target.closest("[data-act]");
    if (!item) return;
    const act = item.dataset.act;
    menu.remove();
    if (act === "pin") sessionTogglePin(i);
    else if (act === "rename") sessionRename(i);
    else if (act === "arch") sessionArchive(i);
    else if (act === "unarch") sessionUnarchive(i);
    else if (act === "del") sessionDelete(i);
  });
}

/* 会话行内重命名（双击或 ✏ 触发） */
function startSessionRename(i, el) {
  const s = state.sessions[i];
  const nameEl = el?.querySelector(".sb-item-name");
  if (!s || !nameEl || nameEl.querySelector("input")) return;
  const input = document.createElement("input");
  input.type = "text";
  input.className = "sb-rename-input";
  input.value = s.name;
  nameEl.replaceChildren(input);
  input.focus();
  input.select();
  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    const val = input.value.trim();
    if (save && val && val !== s.name) {
      s.name = val;
      scheduleSave();
      toast("会话已重命名", "ok");
    }
    renderSessionBar();
  };
  input.addEventListener("keydown", (ev) => {
    ev.stopPropagation();
    if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
    else if (ev.key === "Escape") { ev.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
  input.addEventListener("click", (ev) => ev.stopPropagation());
}

function renderChat() {
  const sess = activeSession();
  const area = $("#chatArea");
  area.innerHTML = "";
  if (!sess.messages.length) {
    area.innerHTML = `<div class="chat-guide">
      <div class="cg-title">💬 与助手讨论你的研究</div>
      <div class="cg-steps">
        <div class="cg-step"><b>①</b> 从左侧「🗂 项目与文件」选择研究项目</div>
        <div class="cg-step"><b>②</b> 拖拽 / 右键把代码、数据、图表加入对话</div>
        <div class="cg-step"><b>③</b> 随时提问碰撞想法；右栏「论文」页准备大纲</div>
      </div>
      <div class="cg-hint">输入框可直接粘贴文本与图片（≤8MB）· ${esc(state.currentProject || "尚未选择项目")}</div>
    </div>`;
  }
  sess.messages.forEach((m, i) => {
    area.appendChild(messageEl(m, i));
  });
  area.scrollTop = area.scrollHeight;
  renderRefBar();
  const cnt = $("#drawerMsgCount");
  if (cnt) cnt.textContent = sess.messages.length ? `${sess.messages.length} 条消息` : "";
}

function messageEl(m, index) {
  const el = document.createElement("div");
  el.className = `msg ${m.role}`;
  const chips = (m.attachments || []).map((a) => `<span class="attach-chip">📎 ${esc(a)}</span>`).join("");
  const body = document.createElement("div");
  body.className = "md";
  body.innerHTML = mdWithFigs(renderTokens(m.content));
  renderFormulas(body);
  el.innerHTML = chips;
  el.appendChild(body);
  if (m.role === "assistant" && m.dur) {  // ZCode 式元信息：须在 innerHTML 之后挂载
    const meta = document.createElement("div");
    meta.className = "msg-meta";
    meta.textContent = `已工作 ${m.dur}${/^\d+$/.test(String(m.dur)) ? " 秒" : ""}`;
    el.insertBefore(meta, el.firstChild);
  }
  const del = document.createElement("span");
  del.className = "msg-del";
  del.textContent = "✕";
  del.title = "删除这条消息";
  del.addEventListener("click", (ev) => {
    ev.stopPropagation();
    const sess = activeSession();
    if (!sess) return;
    sess.messages.splice(Number(del.dataset.i), 1);
    renderChat();
    scheduleSave();  // 全量保存会同步重置后端对话上下文
  });
  del.dataset.i = index;
  el.appendChild(del);
  return el;
}

function addMessage(sess, role, content, attachments = []) {
  sess.messages.push({ role, content, attachments });
  scheduleSave();
}

function addAttachment(path, name, content) {
  let dropId = null;
  if (content) {
    dropId = "drop_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 5);
    state.dropContents = state.dropContents || {};
    state.dropContents[dropId] = content;
  }
  insertFileToken(name, path, dropId);
  toast(`已引用文件：${name}（已插入光标处）`, "ok");
}

/* 光标处插入文件令牌胶囊 */
function insertFileToken(name, path, dropId, isDir) {
  const el = $("#chatInput");
  el.focus();
  const sel = window.getSelection();
  let range;
  if (sel.rangeCount > 0 && el.contains(sel.getRangeAt(0).startContainer)) {
    range = sel.getRangeAt(0);
    range.deleteContents();
  } else {
    range = document.createRange();
    range.selectNodeContents(el);
    range.collapse(false);
  }
  const token = document.createElement("span");
  token.className = "input-token" + (isDir ? " dir-token" : "");
  token.contentEditable = "false";
  token.dataset.name = name;
  if (isDir) token.dataset.isDir = "1";
  if (path) token.dataset.path = path;
  if (dropId) token.dataset.dropId = dropId;
  token.innerHTML = (isDir ? "📁 " : "📎 ") + esc(name);
  range.insertNode(token);
  const after = document.createRange();
  after.setStartAfter(token);
  after.collapse(true);
  sel.removeAllRanges();
  sel.addRange(after);
}

/* 序列化输入框：文本 + [[文件:name]] 标记 + 附件路径/拖入内容 */
function serializeChatInput() {
  const el = $("#chatInput");
  let text = "";
  const paths = [], drops = [], names = [], folders = [];
  (function walk(node) {
    node.childNodes.forEach((n) => {
      if (n.nodeType === 3) { text += n.textContent; return; }
      if (n.nodeType !== 1) return;
      if (n.classList.contains("input-token")) {
        const name = n.dataset.name || n.textContent.replace(/^[📎📁]\s*/, "").trim();
        names.push(name);
        if (n.dataset.isDir) {
          if (n.dataset.path) folders.push(n.dataset.path);
          text += "[[文件夹:" + name + "]]";
          return;
        }
        if (n.dataset.path) paths.push(n.dataset.path);
        if (n.dataset.dropId && state.dropContents && state.dropContents[n.dataset.dropId]) {
          drops.push({ name, content: state.dropContents[n.dataset.dropId] });
        }
        text += "[[文件:" + name + "]]";
        return;
      }
      if (n.tagName === "BR") { text += "\n"; return; }
      walk(n);
    });
  })(el);
  return { text, paths, drops, names, folders };
}

function renderRefBar() {
  const sess = activeSession();
  const bar = $("#attachBar");
  if (!bar || !sess) { if (bar) bar.innerHTML = ""; return; }
  const sels = sess.pendingSelections || [];
  bar.innerHTML = sels.map((s, i) =>
    `<span class="chip sel-chip" title="${esc(s.slice(0, 150))}">📄 论文选段 ${i + 1}<span class="x" data-sel="${i}">✕</span></span>`).join("");
  bar.querySelectorAll("[data-sel]").forEach((el) => el.addEventListener("click", () => {
    const s = activeSession();
    if (s) s.pendingSelections.splice(Number(el.dataset.sel), 1);
    renderRefBar();
  }));
}

/* 拖拽：整个中栏接管（文件树 JSON / 资源管理器文件均可） */
const TEXT_DROP_EXTS = ["md", "txt", "py", "r", "csv", "tsv", "json", "yaml", "yml", "log", "gmt", "tsv", "toml", "html", "htm"];
const midPane = document.querySelector(".main-body");
["dragover", "drop"].forEach((evName) => {
  midPane.addEventListener(evName, (ev) => {
    ev.preventDefault();
    if (evName === "dragover") return;
    // 1) 文件树内部拖拽（JSON 载荷；文件夹→收集全部文件）
    const raw = ev.dataTransfer.getData("text/plain");
    if (raw) {
      try {
        const data = JSON.parse(raw);
        if (data.isDir) { collectAndInsertDir(data.path, data.name); return; }
        if (data.path) { addAttachment(data.path, data.name); return; }
      } catch (e) { /* 非文件树拖拽 */ }
    }
    // 2) 资源管理器拖入的本地文件（读内容）
    const files = Array.from(ev.dataTransfer.files || []);
    if (files.length) handleOsFiles(files);
  });
});

async function handleOsFiles(files) {
  for (const f of files.slice(0, 6)) {
    const ext = f.name.split(".").pop().toLowerCase();
    if (isImageFile(f.name)) {
      // 图片 → dataURL 引用（发给视觉代理查看）
      if (f.size > 8 * 1024 * 1024) { toast(`「${f.name}」超过 8MB`, "err"); continue; }
      try {
        const dataUrl = await new Promise((res, rej) => {
          const r = new FileReader();
          r.onload = () => res(r.result);
          r.onerror = () => rej(new Error("读取失败"));
          r.readAsDataURL(f);
        });
        addAttachment(null, f.name, dataUrl);
      } catch (e) { toast(`读取「${f.name}」失败`, "err"); }
      continue;
    }
    if (f.size > 300 * 1024) { toast(`「${f.name}」超过 300KB，请从文件树添加（走路径引用）`, "err"); continue; }
    if (!TEXT_DROP_EXTS.includes(ext)) { toast(`「${f.name}」不是文本格式，仅支持 ${TEXT_DROP_EXTS.join("/")}`, "err"); continue; }
    try {
      const content = await f.text();
      addAttachment(null, f.name, content);
    } catch (e) { toast(`读取「${f.name}」失败`, "err"); }
  }
}

const SKIP_BIN_EXTS = ["pkl", "h5", "hdf5", "npy", "npz", "zip", "gz", "tar", "7z", "rar", "exe", "dll", "rds", "rda", "sqlite", "db", "bin", "parquet", "idx", "bak"];
function findNodeByPath(node, path) {
  if (node.path === path) return node;
  for (const c of node.children || []) { const f = findNodeByPath(c, path); if (f) return f; }
  return null;
}

async function collectAndInsertDir(dirPath, dirName) {
  if (!state.currentProject) { toast("请先选择项目", "err"); return; }
  insertFileToken(dirName, dirPath, null, true);
  toast(`已添加文件夹引用：${dirName}（随下一条消息发送结构清单）`, "ok");
}
/* 发送消息 */
async function sendMessage() {
  const input = $("#chatInput");
  const question = serializeChatInput().text.trim();
  if (!question) return;
  const sess = activeSession();
  const ser = serializeChatInput();
  const attachments = ser.paths;
  const file_drops = ser.drops;
  const folders = ser.folders;
  const selections = (sess.pendingSelections || []).slice(0, 6);
  const attachNames = ser.names;

  addMessage(sess, "user", ser.text, attachNames);
  input.innerHTML = "";
  renderChat();

  const replyEl = document.createElement("div");
  replyEl.className = "msg assistant";
  replyEl.innerHTML = '<div class="agent-actions"></div><div class="md cursor-blink"></div>';
  const _t0 = Date.now();
  $("#chatArea").appendChild(replyEl);
  $("#chatArea").scrollTop = $("#chatArea").scrollHeight;
  $("#btnSend").disabled = true;

  let acc = "";
  let needsPaperRefresh = false;
  let editConflictToasted = false;
  const pendingRegens = [];
  let pendingExport = null;
  const bodyEl = replyEl.querySelector(".md");
  const actBox = replyEl.querySelector(".agent-actions");
  try {
    await streamSSE("/api/chat/stream",
      { question, session_id: sess.id, attachments, selections, file_drops, folders,
        project_name: sess.project || state.currentProject || null },
      (ev) => {
        if (ev.type === "status") {
          if (!acc) bodyEl.innerHTML = '<i style="color:var(--text-dim)">🔍 ' + esc(ev.content) + '</i>';
        } else if (ev.type === "action_start") {
          const row = document.createElement("div");
          row.className = "act-row pending";
          row.innerHTML = `<span class="spinner"></span> 🔧 ${esc(ev.label || "执行动作")}…`;
          actBox.appendChild(row);
          $("#chatArea").scrollTop = $("#chatArea").scrollHeight;
        } else if (ev.type === "action_result") {
          const row = actBox.querySelector(".act-row.pending:last-of-type") || actBox.lastElementChild;
          if (row) {
            row.className = `act-row ${ev.ok ? "ok" : "err"}`;
            row.innerHTML = `${ev.ok ? "✅" : "❌"} <b>${esc(ev.label || "")}</b> ${esc(ev.summary || "")}`;
          }
          const aff = ev.affects || {};
          if (aff.section_id) {
            if (state.editingSid === aff.section_id && !editConflictToasted) {
              editConflictToasted = true;
              toast("对话 Agent 刚修改了正在编辑的节：编辑器里仍是旧稿，保存前请先核对", "err");
            }
            needsPaperRefresh = true;
          }
          if (aff.layout) {
            state.paperStyle = { ...PAPER_STYLE_DEFAULTS, ...aff.layout };
            applyPaperStyle();
            scheduleSave();
          }
          if (aff.export) pendingExport = aff.export;
          if (aff.regen && aff.regen.section_id) pendingRegens.push(aff.regen);
          $("#chatArea").scrollTop = $("#chatArea").scrollHeight;
        } else if (ev.type === "chunk") {
          acc += ev.content;
          bodyEl.innerHTML = md(acc);
          $("#chatArea").scrollTop = $("#chatArea").scrollHeight;
        } else if (ev.type === "error") {
          const tail = acc ? "\n\n⚠️ " + ev.content : "⚠️ " + ev.content;
          acc += tail;
          bodyEl.innerHTML = md(acc);
        }
      });
    bodyEl.classList.remove("cursor-blink");
    const durSec = Math.max(1, Math.round((Date.now() - _t0) / 1000));
    addMessage(sess, "assistant", acc);
    sess.messages[sess.messages.length - 1].dur = durSec;
    const metaEl = document.createElement("div");
    metaEl.className = "msg-meta";
    metaEl.textContent = `已工作 ${durSec} 秒`;
    replyEl.prepend(metaEl);
    renderTaskProgress();
    sess.pendingSelections = [];
    renderRefBar();
    // 动作副作用：刷新论文区 → 导出 → 安排重写（按序执行）
    if (needsPaperRefresh) {
      await tryLoadOutline(state.currentProject);
      renderOutline();
    }
    if (pendingExport) exportPaper(pendingExport);
    for (const rg of pendingRegens) {
      const card = document.querySelector(`.section-card[data-sid="${rg.section_id}"]`);
      const sec = state.outline?.sections.find((s) => s.id === rg.section_id);
      if (sec && !(sec.status === "drafted" && !sec.content)) {
        regenerateSection(rg.section_id, rg.instruction || "按对话要求重写本节", card);
      } else if (sec) {
        regenerateSection(rg.section_id, rg.instruction || "按对话要求生成本节", card);
      }
    }
  } catch (e) {
    bodyEl.classList.remove("cursor-blink");
    bodyEl.innerHTML = md(`⚠️ 请求失败：${e.message}`);
    addMessage(sess, "assistant", `⚠️ 请求失败：${e.message}`);
  } finally {
    $("#btnSend").disabled = false;
    $("#chatArea").scrollTop = $("#chatArea").scrollHeight;
  }
}

/* ================= 参考论文 ================= */
const _truncOpt = (t, n = 24) => (t && t.length > n ? t.slice(0, n) + "…" : t || "");
function syncRefSelectTitle() {
  const sel = $("#refSelect");
  const opt = sel?.selectedOptions?.[0];
  if (sel) sel.title = opt && opt.value ? (opt.dataset.full || opt.textContent)
                                        : "选择生成大纲时借鉴结构的参考论文";
}
async function refreshRefList() {
  try {
    const resp = await api("/api/reference/list");
    const sel = $("#refSelect");
    const current = sel.value;
    sel.innerHTML = '<option value="">不使用参考论文</option>' +
      (resp.papers || []).map((p) => {
        const full = p.title || p.original_filename || p.name;
        return `<option value="${esc(p.name)}" data-full="${esc(full)}">${esc(_truncOpt(full))}（${p.sections_count}节）</option>`;
      }).join("");
    if (current) sel.value = current;
    syncRefSelectTitle();
  } catch (e) { /* 静默 */ }
}

async function uploadReference(file) {
  const btn = $("#btnUploadRef");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 解析中';
  toast(`正在解析「${file.name}」的章节结构（约10-30秒）…`);
  try {
    const fd = new FormData();
    fd.append("file", file);
    const resp = await fetch("/api/reference/upload", { method: "POST", headers: authHeaders(), body: fd });
    if (!resp.ok) {
      let detail = resp.statusText;
      try { detail = (await resp.json()).detail || detail; } catch (e) { /* ignore */ }
      throw new Error(detail);
    }
    const data = await resp.json();
    toast(`解析完成：《${data.summary.title || file.name}》共 ${data.summary.sections_count} 节`
      + (data.format_profiled ? " · 格式档案✓" : "")
      + " · 写作骨架后台提取中", "ok");
    await refreshRefList();
    const sel = $("#refSelect");
    sel.value = data.summary.name;
    loadTemplatePanel();  // 上传即触发：格式档案立即可见，骨架轮询至完成
  } catch (e) {
    toast(`参考论文解析失败: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
    btn.innerHTML = "📎 参考论文";
  }
}

/* ================= 模板论文双通道档案（通道A 格式模板 / 通道B 写作骨架） ================= */
const tplState = { name: "", timer: null, format: null, skeleton: null, applied: false };

function _setChip(el, cls, text) {
  if (!el) return;
  el.className = "tpl-chip" + (cls ? " " + cls : "");
  el.textContent = text;
}

function tplModal(title, bodyHtml) {
  const old = document.querySelector("#tplModal");
  if (old) old.remove();
  const ov = document.createElement("div");
  ov.id = "tplModal";
  ov.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:9999;"
    + "display:flex;align-items:center;justify-content:center;padding:24px;";
  ov.innerHTML = `<div style="background:var(--bg,#1e1e2e);color:var(--text,#cdd6f4);border:1px solid rgba(128,128,128,.35);border-radius:12px;max-width:680px;width:100%;max-height:80vh;display:flex;flex-direction:column;box-shadow:0 12px 40px rgba(0,0,0,.5);overflow:hidden;">
    <div style="display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid rgba(128,128,128,.25);">
      <b>${esc(title)}</b>
      <button class="btn-mini" data-close style="flex:none;">✕ 关闭</button>
    </div>
    <div style="padding:14px 16px;overflow-y:auto;font-size:13px;line-height:1.7;white-space:pre-wrap;">${bodyHtml}</div>
  </div>`;
  ov.addEventListener("click", (ev) => {
    if (ev.target === ov || ev.target.closest("[data-close]")) ov.remove();
  });
  document.body.appendChild(ov);
}

async function loadTemplatePanel() {
  const sel = $("#refSelect");
  const name = sel ? sel.value : "";
  const panel = $("#tplPanel");
  if (!panel) return;
  if (!name) {
    panel.style.display = "none";
    if (tplState.timer) { clearInterval(tplState.timer); tplState.timer = null; }
    return;
  }
  panel.style.display = "";
  if (tplState.name !== name && tplState.timer) {
    clearInterval(tplState.timer);
    tplState.timer = null;
    tplState.format = null;
    tplState.skeleton = null;
  }
  tplState.name = name;
  const opt = sel.selectedOptions?.[0];
  const full = opt ? (opt.dataset.full || opt.textContent) : "";
  const nameEl = $("#tplName");
  nameEl.textContent = full || "（未知模板）";
  nameEl.title = full || "";  // CSS 两行截断，悬停看全名
  // 通道A：格式档案（上传时已同步提取）
  try {
    const f = await api(`/api/reference/${encodeURIComponent(name)}/format`);
    tplState.format = f;
    const conf = f.profile?.source?.confidence;
    if (conf === "high") _setChip($("#tplFormatBadge"), "ok", "✓ 高精度");
    else if (conf === "medium") _setChip($("#tplFormatBadge"), "warn", "≈ PDF 近似");
    else _setChip($("#tplFormatBadge"), "err", "⚠ 低置信");
  } catch (e) {
    _setChip($("#tplFormatBadge"), "err", "✗ 不支持");
    tplState.format = null;
  }
  // 通道B：写作骨架（running 时 5s 轮询直到完成）
  await pollSkeleton(name);
  _ensureSkeletonPolling(name);
  await refreshAppliedStyle();
}

function _ensureSkeletonPolling(name) {
  if (tplState.timer || tplState.skeleton?.status?.state !== "running") return;
  tplState.timer = setInterval(async () => {
    await pollSkeleton(name);
    const st = tplState.skeleton?.status?.state;
    if (st !== "running" || $("#refSelect").value !== name) {
      if (tplState.timer) { clearInterval(tplState.timer); tplState.timer = null; }
      if (st === "done") toast("写作骨架提取完成（生成大纲时自动套用叙事形式）", "ok");
      if (st === "error") toast(`写作骨架提取失败：${tplState.skeleton?.status?.error || ""}`, "err");
    }
  }, 5000);
}

async function pollSkeleton(name) {
  const extractBtn = $("#btnSkelExtract");
  try {
    const s = await api(`/api/reference/${encodeURIComponent(name)}/skeleton`);
    tplState.skeleton = s;
    const st = s.status?.state;
    if (st === "done") {
      _setChip($("#tplSkeletonBadge"), "ok", `✓ ${s.skeleton?.sections?.length || 0} 槽位`);
    } else if (st === "running") {
      _setChip($("#tplSkeletonBadge"), "run", "⏳ 提取中…");
    } else if (st === "error") {
      _setChip($("#tplSkeletonBadge"), "err", "✗ 失败");
    } else {
      _setChip($("#tplSkeletonBadge"), "", "未提取");
    }
    if (extractBtn) {
      extractBtn.style.display = "";
      extractBtn.disabled = st === "running";
      extractBtn.textContent = st === "error" ? "重试提取" : (st === "done" ? "重新提取" : "提取骨架");
      // 未提取/失败时提为主动作（primary）；已有骨架时降为普通次按钮
      extractBtn.classList.toggle("primary", st === "idle" || st === "error");
    }
  } catch (e) {
    _setChip($("#tplSkeletonBadge"), "err", "✗");
    if (extractBtn) {
      extractBtn.style.display = ""; extractBtn.disabled = false;
      extractBtn.textContent = "重试提取"; extractBtn.classList.add("primary");
    }
  }
}

async function extractSkeleton() {
  const name = $("#refSelect")?.value;
  if (!name) { toast("请先选择模板论文", "err"); return; }
  const btn = $("#btnSkelExtract");
  btn.disabled = true;
  try {
    await api(`/api/reference/${encodeURIComponent(name)}/skeleton/extract`, { method: "POST" });
    toast("写作骨架后台提取中（约 1-2 分钟，状态自动刷新）", "ok");
    tplState.skeleton = { status: { state: "running" } };
    _ensureSkeletonPolling(name);
    pollSkeleton(name);
  } catch (e) {
    toast(`骨架提取启动失败: ${e.message}`, "err");
    btn.disabled = false;
  }
}

async function refreshAppliedStyle() {
  const bar = $("#tplAppliedBar");
  const hint = $("#tplHint");
  const ignoreBtn = $("#btnTplIgnore");
  if (!bar || !hint) return;
  if (!state.currentProject) return;
  try {
    const r = await api(`/api/export/style?project_name=${encodeURIComponent(state.currentProject)}`);
    tplState.applied = !!r.applied;
    bar.classList.toggle("applied", !!r.applied);
    if (r.applied) {
      hint.textContent = `✓ 已套入排版 · ${r.source_title || r.source_name || ""}（导出自动生效）`;
    } else {
      hint.textContent = "未套入 · 导出使用系统默认排版";
    }
    if (ignoreBtn) ignoreBtn.disabled = !r.applied;  // 未套入时无可撤销 → 置灰
  } catch (e) { /* 静默 */ }
}

function openTplPreview() {
  const f = tplState.format;
  if (!f) { toast("格式档案尚未提取（仅支持 docx/pdf 模板）", "err"); return; }
  const lines = (f.summary || []).map((s) => `· ${esc(s)}`).join("\n") || "（无可读摘要）";
  const keys = Object.entries(f.style || {}).map(([k, v]) =>
    `<tr><td style="padding:2px 10px 2px 0;color:var(--text-dim,#888);">${esc(k)}</td><td style="padding:2px 0;">${esc(String(v))}</td></tr>`).join("");
  tplModal("🎨 格式模板档案",
    `${lines}\n\n<b>编译后的导出键（一键套入后生效）</b>\n<table style="border-collapse:collapse;font-size:12px;margin-top:6px;">${keys}</table>`);
}

function openSkelPreview() {
  const s = tplState.skeleton?.skeleton;
  if (!s) {
    const st = tplState.skeleton?.status?.state;
    toast(st === "running" ? "写作骨架正在提取（1-2 分钟），稍后再看" : "写作骨架尚未提取", "err");
    return;
  }
  let html = `<b>体裁</b>：${esc(s.genre || "")}\n<b>叙事弧</b>：${esc(s.macro_arc || "")}\n\n`;
  html += (s.sections || []).map((sec) => {
    const moves = (sec.moves || []).map((m, i) => `&nbsp;&nbsp;${i + 1}. ${esc(String(m))}`).join("<br>");
    const bar = Math.round((sec.word_share || 0) * 100);
    return `<div style="margin-bottom:8px;padding:8px 10px;border:1px solid rgba(128,128,128,.25);border-radius:8px;">
      <b>${esc(sec.slot || "")}</b> <span style="color:var(--text-dim,#888);">（${esc(sec.role || "")}）</span><span style="float:right;font-variant-numeric:tabular-nums;">${bar}%</span>
      <div style="height:4px;border-radius:2px;background:rgba(128,128,128,.25);margin:4px 0;overflow:hidden;">
        <div style="height:4px;width:${Math.min(100, bar)}%;background:#7aa2f7;"></div>
      </div>${moves}
      ${sec.progression ? `<div style="color:var(--text-dim,#888);margin-top:4px;">递进：${esc(sec.progression)}</div>` : ""}
    </div>`;
  }).join("");
  const links = s.cross_section_links || [];
  if (links.length) {
    html += `\n<b>跨章承接（环环相扣）</b>\n` + links.map((l) =>
      `· ${esc(l.from)} → ${esc(l.to)}（${esc(l.type)}）：${esc(l.note || "")}`).join("\n");
  }
  const rh = s.rhetoric || {};
  if (Object.keys(rh).length) {
    html += `\n<b>修辞规范</b>\n` + Object.entries(rh).map(([k, v]) =>
      `· ${esc(k)}: ${esc(String(Array.isArray(v) ? v.join("~") : v))}`).join("\n");
  }
  tplModal("🦴 写作骨架（叙事形式 · 内容无关）", html);
}

async function applyTemplate() {
  const name = $("#refSelect")?.value;
  if (!name) { toast("请先选择模板论文", "err"); return; }
  if (!state.currentProject) { toast("请先选择研究项目（套入是项目级设置）", "err"); return; }
  if (!tplState.format) { toast("该论文没有格式档案（仅 docx/pdf 支持）", "err"); return; }
  const btn = $("#btnTplApply");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 套入中';
  try {
    const r = await api("/api/export/style/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_name: state.currentProject, reference_name: name }),
    });
    toast(`已套入「${r.source_title || name}」排版（${Object.keys(r.style || {}).length} 项，导出自动生效）`, "ok");
    await refreshAppliedStyle();
  } catch (e) {
    toast(`一键套入失败: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
    btn.innerHTML = "一键套入";
  }
}

async function ignoreTemplate() {
  if (!state.currentProject) { toast("请先选择研究项目", "err"); return; }
  if (!tplState.applied) { toast("本项目当前未套入模板排版", ""); return; }
  const ok = await ui.confirm({
    title: "撤销模板排版", icon: "↩",
    html: `「<b>${esc($("#tplName").title || "当前模板")}</b>」的套入将被清除，导出回到系统默认排版。<br><span style="font-size:11.5px;color:var(--text-faint)">不影响大纲与正文内容</span>`,
    confirmText: "撤销套入",
  });
  if (!ok) return;
  try {
    await api("/api/export/style/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_name: state.currentProject }),
    });
    toast("已清除模板排版，回到系统默认", "ok");
    await refreshAppliedStyle();
  } catch (e) {
    toast(`操作失败: ${e.message}`, "err");
  }
}

/* ================= 大纲 ================= */
async function tryLoadOutline(projectName) {
  try {
    state.outline = await api(`/api/outline/${encodeURIComponent(projectName)}`);
    return true;
  } catch (e) {
    state.outline = null;
    return false;
  }
}

async function generateOutline() {
  if (!state.currentProject) { toast("请先选择研究项目", "err"); return; }
  if (state.outlineGenerating) return;
  // 骨架提取中提醒（不阻断）：等它完成可让大纲更贴合模板叙事
  const skelState = tplState.skeleton?.status?.state;
  if (skelState === "running") {
    toast("写作骨架仍在提取中，本大纲将不套用骨架（可稍后重新生成以套用）", "");
  } else if (skelState === "idle" && $("#refSelect")?.value) {
    toast("该参考论文尚未提取写作骨架，大纲仅借鉴章节组织（可在模板档案卡手动提取）", "");
  }
  // 字数约束：3万~8万，吸附到5000分档
  const raw = Number($("#totalWords")?.value) || 30000;
  const clamped = Math.min(80000, Math.max(30000, raw));
  const total = Math.round(clamped / 5000) * 5000;
  $("#totalWords").value = total;
  state.outlineGenerating = true;
  setGenerating(true);
  const btn = $("#btnGenerateOutline");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 生成中…';
  $("#outlineArea").innerHTML = Array(5).fill('<div class="skeleton"></div>').join("") +
    '<div class="empty-hint"><span class="spinner"></span> 正在基于项目画像与写作知识库生成大纲…</div>';
  try {
    const resp = await api("/api/outline/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_name: state.currentProject,
        template: $("#templateSelect")?.value || "chinese_journal",
        total_words: total,
        user_requirements: "",
        reference_paper: $("#refSelect")?.value || null,
        domain: ($("#domainSelect")?.value === "auto") ? null : ($("#domainSelect")?.value || null),
      }),
    });
    state.outline = resp.outline;
    renderOutline();
    scheduleSave();
    toast("大纲生成完成", "ok");
    switchWsTab("outline"); // 大纲已是正式视图，生成完成自动切到大纲标签供逐节操作
    // 骨架对齐提示：未覆盖槽位说明（不阻断）
    const align = resp.outline?.skeleton_alignment;
    if (align?.unmatched_slots?.length) {
      toast(`写作骨架槽位「${align.unmatched_slots.join("、")}」未在大纲中匹配（以项目内容为准）`, "");
    }
  } catch (e) {
    toast(`大纲生成失败: ${e.message}`, "err");
    $("#outlineArea").innerHTML = `<div class="empty-hint">生成失败: ${esc(e.message)}</div>`;
  } finally {
    state.outlineGenerating = false;
    setGenerating(false);
    btn.disabled = false;
    btn.innerHTML = "✨ 生成大纲";
  }
}

/* ================= 清空论文 ================= */
async function clearPaper() {
  if (!state.currentProject) { toast("请先选择研究项目", "err"); return; }
  if (!state.outline) { toast("当前项目还没有论文（无大纲）", ""); return; }
  const drafted = (state.outline.sections || []).filter((s) => (s.content || "").trim()).length;
  const total = state.outline.sections.length;
  // 单对话框三选择，明确语义（旧版两连 confirm「取消=彻底清空」语义反转，极易误操作）
  const act = await ui.dialog({
    title: "清空论文", icon: "🗑", width: 440,
    html: `当前 <b>${total}</b> 章大纲，已成稿 <b>${drafted}</b> 章正文。请选择清空范围：<br>
      <span style="font-size:11.5px;color:var(--text-faint)">彻底清空不可恢复，需要输入确认文字</span>`,
    actions: [
      { label: "取消", value: "cancel", kind: "ghost" },
      { label: `仅清空正文（保留 ${total} 章大纲）`, value: "content", kind: "primary" },
      { label: "彻底清空（连大纲删除）", value: "full", kind: "danger" },
    ],
  });
  if (act === "cancel" || !act) return;
  if (act === "full") {
    const ok = await ui.confirm({
      title: "彻底清空确认", icon: "⚠", danger: true,
      message: "将删除全部大纲与正文，回到未生成状态。此操作不可恢复！",
      confirmText: "彻底清空", requireText: "删除",
    });
    if (!ok) return;
  }
  return _doClearPaper(act);
}

async function _doClearPaper(mode) {
  try {
    const r = await api(`/api/outline/${encodeURIComponent(state.currentProject)}?mode=${mode}`,
      { method: "DELETE" });
    if (mode === "full") {
      state.outline = null;
      renderOutline();
      toast("论文已彻底清空（大纲已删除）", "ok");
    } else {
      await tryLoadOutline(state.currentProject);
      renderOutline();
      toast(`正文已清空（保留 ${r.sections} 章大纲，清除成稿 ${r.cleared_drafts} 章）`, "ok");
    }
    scheduleSave();
  } catch (e) {
    toast(`清空失败: ${e.message}`, "err");
  }
}

/* ================= 论文工作区（v24）：[论文|大纲] 标签 + 配置滑出面板 =================
   大纲从弹窗搬回正式视图：#outlineArea 常驻「大纲」标签下，渲染与逐卡片事件绑定零改动。 */
function switchWsTab(tab) {
  document.querySelectorAll(".ws-tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.ws === tab));
  document.querySelectorAll(".ws-view").forEach((v) =>
    v.classList.toggle("active", v.dataset.wsView === tab));
}
function bindWorkspaceTabs() {
  document.querySelectorAll(".ws-tab").forEach((b) =>
    b.addEventListener("click", () => switchWsTab(b.dataset.ws)));
}

/* 工作区头部下拉菜单：工具（含隔离的危险操作）/ 导出三件套 */
function bindWorkspaceMenus() {
  $("#btnToolsMenu")?.addEventListener("click", async (ev) => {
    const anchor = ev.currentTarget;
    const v = await ui.menu(anchor, [
      { icon: "🔍", label: "全文自查", value: "review", hint: "模拟审稿报告" },
      { icon: "🛡", label: "AI 检测中心", value: "aic", hint: "双引擎体检" },
      { icon: "🖼", label: "图片整理", value: "fig", hint: "题注/换图" },
      { icon: "📐", label: "排版设置", value: "style", hint: "字号/行距" },
      { sep: true },
      { icon: "🗑", label: "清空论文", value: "clear", danger: true, hint: "危险操作" },
    ]);
    if (v === "review") selfReview();
    else if (v === "aic") openAiCenter();
    else if (v === "fig") openFigureWorkshop();
    else if (v === "style") openStyleMenu(anchor);
    else if (v === "clear") clearPaper();
  });
  $("#btnExportMenu")?.addEventListener("click", async (ev) => {
    const v = await ui.menu(ev.currentTarget, [
      { icon: "📝", label: "Markdown", value: "md" },
      { icon: "📄", label: "Word（.docx）", value: "docx", hint: "推荐" },
      { icon: "🖨", label: "PDF", value: "pdf", hint: "较慢" },
    ]);
    if (v) exportPaper(v);
  });
}
function openCfgPanel() {
  $("#taskPanel")?.classList.add("open");
  $("#paperPane")?.classList.add("cfg-open");
}
function closeCfgPanel() {
  $("#taskPanel")?.classList.remove("open");
  $("#paperPane")?.classList.remove("cfg-open");
}

/* ================= 准备向导（无大纲时的论文视图） =================
   4 步准备流程可视化：完成状态实时来自 state，点击步骤直达对应操作。 */
function prepWizardSteps() {
  const hasRef = !!($("#refSelect")?.value || "");
  const steps = [];
  if (!state.currentProject) {
    steps.push({
      key: "project", icon: "🗂", title: "选择研究项目",
      desc: "从左侧「项目与文件」抽屉选择（或注册）你的代码/实验项目",
      action: "选择项目", done: false,
      run: () => { openPaneView("projects"); toast("点击一个项目目录完成注册"); },
    });
  }
  steps.push(
    {
      key: "settings", icon: "⚙", title: "论文设置",
      desc: `总字数 ${$("#totalWords")?.value || 30000} 字 · 结构模板与领域（默认自动检测）`,
      action: "配置", done: !!state.currentProject,
      run: openCfgPanel,
    },
    {
      key: "reference", icon: "📎", title: "参考论文", optional: true,
      desc: "上传一篇已发表论文，自动提取格式排版与写作骨架供借鉴（可跳过）",
      action: hasRef ? "查看" : "上传", done: hasRef,
      run: () => { openCfgPanel(); $("#refFileInput")?.click(); },
    },
    {
      key: "understand", icon: "🧠", title: "项目理解",
      desc: "Agent 通读项目 → 澄清模糊点 → 生成《研究项目主体》注入全部写作任务",
      action: state.projectUnderstood ? "重新理解" : "理解项目", done: !!state.projectUnderstood,
      run: () => understandProject(false),
    },
    {
      key: "outline", icon: "✨", title: "生成大纲",
      desc: "基于项目画像 + 知识库生成章节大纲，随后逐节写作或一键生成全文",
      action: "生成大纲", done: false,
      run: () => generateOutline(),
    },
  );
  return steps;
}
function renderPrepWizard() {
  const steps = prepWizardSteps();
  // 当前步骤 = 第一个未完成且非可选的步骤
  const activeIdx = steps.findIndex((s) => !s.done && !s.optional);
  const rows = steps.map((s, i) => {
    const st = s.done ? "done" : i === activeIdx ? "active" : s.optional ? "optional" : "pending";
    const no = s.done ? "✓" : String(i + 1);
    return `<div class="pw-step ${st}" data-pw="${i}">
      <span class="pw-no">${no}</span>
      <div class="pw-main">
        <div class="pw-title">${s.icon} ${s.title}${s.optional ? '<span class="pw-opt">可选</span>' : ""}</div>
        <div class="pw-desc">${s.desc}</div>
      </div>
      <button class="btn-mini ${i === activeIdx ? "primary" : ""}" data-pw-run="${i}">${s.action}</button>
    </div>`;
  }).join("");
  const proj = state.currentProject
    ? `当前项目：<b>${esc(state.currentProject)}</b>`
    : "尚未选择项目";
  return `<div class="prep-wizard">
    <div class="pw-hero">
      <div class="pw-hero-ico">
        <svg viewBox="0 0 32 32" width="34" height="34" aria-hidden="true">
          <path d="M16 6.2 C13.1 11.0 10.4 14.2 10.4 17.7 a5.6 5.6 0 0 0 11.2 0 C21.6 14.2 18.9 11.0 16 6.2 Z" fill="var(--accent)"/>
          <path d="M10.8 26.2 h10.4" stroke="var(--accent)" stroke-width="1.9" stroke-linecap="round" opacity="0.8"/>
        </svg>
      </div>
      <div class="pw-hero-title">开始你的论文</div>
      <div class="pw-hero-sub">按以下步骤准备，大纲生成后即可逐节写作 · ${proj}</div>
    </div>
    <div class="pw-steps">${rows}</div>
    <div class="pw-foot-hint">💡 左侧对话窗随时可讨论：拖入项目文件与助手碰撞想法，再回来生成大纲</div>
  </div>`;
}
function bindPrepWizard(view) {
  const steps = prepWizardSteps();
  view.querySelectorAll("[data-pw-run]").forEach((btn) =>
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      steps[Number(btn.dataset.pwRun)]?.run();
    }));
}

function renderOutline() {
  const outline = state.outline;
  const area = $("#outlineArea");
  renderTaskProgress();
  if (!outline) {
    area.innerHTML = '<div class="empty-hint">在「论文」页完成准备步骤后点击「✨ 生成大纲」<br>大纲章节卡片将在这里展开：逐节生成 / 润色 / 去AI味 / 注入会话</div>';
    $("#wordsSummary").textContent = "未生成大纲";
    $("#progressFill").style.width = "0%";
    return;
  }
  const totalActual = outline.sections.reduce((s, x) => s + (x.actual_words || 0), 0);
  const totalTarget = outline.total_target_words || outline.sections.reduce((s, x) => s + (x.target_words || 0), 0);
  const pct = totalTarget ? Math.min(100, Math.round((totalActual / totalTarget) * 100)) : 0;
  $("#progressFill").style.width = `${pct}%`;
  $("#wordsSummary").textContent =
    `${outline.template_name || ""} · 已写 ${totalActual} / 目标 ${totalTarget} 字（${pct}%）`;

  area.innerHTML = "";
  // 递进门禁（游标）：第一个未成稿章节之后的所有待生成章节锁定——
  // 论文环环相扣，前节正文与关键数字是后节的直接上下文，禁止跳章生成
  const cursor = outline.sections.findIndex((s) => !((s.content || "").trim()));
  outline.sections.forEach((sec, i) => {
    const card = document.createElement("div");
    card.className = "section-card";
    card.dataset.sid = sec.id;
    const done = sec.status === "drafted";
    const locked = !done && cursor >= 0 && i > cursor;
    const wordsCls = done ? "words done" : "words";
    const badge = _badgeText(sec);
    const subList = (sec.subsections || []).map((sub) => {
      const sdone = sub.status === "drafted" && sub.content;
      const subs3 = (sub.subsections || []).map((c3) => {
        const d3 = c3.status === "drafted" && c3.content;
        return `<li class="sub-row l3 ${d3 ? "done" : ""}">
          <span class="sub-name">${esc(c3.title)}</span>
          <span class="sub-words">${d3 ? (c3.actual_words || 0) + "字" : "待生成"}</span>
          <button class="btn-mini sub-regen" data-subregen="${sec.id}|${esc(c3.id)}" title="仅重写该小节" ${done ? "" : "disabled"}>🔄</button>
        </li>`;
      }).join("");
      return `<li class="sub-row l2 ${sdone ? "done" : ""}">
        <span class="sub-name">${esc(sub.title)}</span>
        <span class="sub-words">${sdone ? (sub.actual_words || 0) + "/" + (sub.target_words || "?") + "字" : "目标 " + (sub.target_words || "?") + "字"}</span>
        <button class="btn-mini sub-regen" data-subregen="${sec.id}|${esc(sub.id)}" title="仅重写该小节（其余小节保留）" ${done ? "" : "disabled"}>🔄</button>
      </li>${subs3 ? `<ul class="sub-list l3">${subs3}</ul>` : ""}`;
    }).join("");
    card.innerHTML = `
      <div class="head">
        <span class="sec-dot ${done ? "done" : ""}"></span>
        <span class="title">${i + 1}. ${esc(sec.title)}</span>
        ${done ? `<span class="ai-badge" data-aicheck="${sec.id}" title="AI味体检 · 点击看报告">${badge}</span>` : ""}
        <span class="${wordsCls} pill">${done ? `${sec.actual_words}/${sec.target_words}` : `目标 ${sec.target_words}`}</span>
      </div>
      <div class="body">
        <ul class="keypoints">${(sec.key_points || []).map((p) => `<li>${esc(p)}</li>`).join("")}</ul>
        ${subList ? `<ul class="sub-list l2">${subList}</ul>` : ""}
        <div class="content-md">${sec.content ? mdWithFigs(sec.content) : '<div class="placeholder">尚未生成正文</div>'}</div>
        <div class="regenerate-box">
          <textarea placeholder="重生成要求（可选）：如'更精炼''补充亚组分析结果'"></textarea>
          <div class="ai-report" id="ai-report-${sec.id}" style="display:none;"></div>
          <div class="section-actions">
            <button class="btn-mini primary" data-regen="${sec.id}" ${locked
              ? 'disabled title="递进门禁：前置章节尚未生成。论文须环环相扣递进论述（前节正文与关键数字是本节上下文），请按顺序先生成。"'
              : ""}>${locked ? "🔒 前置未完成" : `🔄 ${done ? "重新生成" : "生成本节"}`}</button>
            <button class="btn-mini" data-polish="${sec.id}" ${done ? "" : "disabled"} title="AI润色（提升学术表达）">✨ 润色</button>
            <button class="btn-mini" data-humanize="${sec.id}" ${done ? "" : "disabled"} title="去AI味（消除机器痕迹）">🪄 去AI味</button>
            <button class="btn-mini" data-inject="${sec.id}">📥 注入会话</button>
            ${sec.pre_enhance_content && sec.content && sec.content !== sec.pre_enhance_content
              ? `<button class="btn-mini" data-restore="${sec.id}" title="还原为增强前原文">↩ 还原</button>` : ""}
          </div>
        </div>
      </div>`;
    card.querySelector(".head").addEventListener("click", () => card.classList.toggle("open"));
    card.querySelector("[data-regen]").addEventListener("click", (ev) => {
      ev.stopPropagation();
      const instruction = card.querySelector("textarea").value.trim();
      regenerateSection(sec.id, instruction, card);
    });
    card.querySelector("[data-inject]").addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (!sec.content) { toast("该节还没有正文", "err"); return; }
      const sess = activeSession();
      addMessage(sess, "user", `请审阅/讨论论文「${sec.title}」一节：\n\n${sec.content}`);
      renderChat();
      toast(`「${sec.title}」已注入当前会话`, "ok");
    });
    const polishBtn = card.querySelector("[data-polish]");
    const humanBtn = card.querySelector("[data-humanize]");
    if (polishBtn) polishBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const r = polishBtn.getBoundingClientRect();
      const x = ev.clientX || (r.left + r.width / 2);
      const y = ev.clientY || (r.bottom + 4);
      showStrengthMenu(x, y, sec.id, "polish");
    });
    if (humanBtn) humanBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const r = humanBtn.getBoundingClientRect();
      const x = ev.clientX || (r.left + r.width / 2);
      const y = ev.clientY || (r.bottom + 4);
      showStrengthMenu(x, y, sec.id, "humanize");
    });
    const restoreBtn = card.querySelector("[data-restore]");
    if (restoreBtn) restoreBtn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      await restoreSection(sec.id, card);
    });
    const badgeEl = card.querySelector("[data-aicheck]");
    if (badgeEl) badgeEl.addEventListener("click", (ev) => {
      ev.stopPropagation();
      toggleAiReport(sec.id, card);
    });
    // 小节局部重生成（多级大纲）
    card.querySelectorAll("[data-subregen]").forEach((btn) => {
      btn.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        const [sid, subId] = btn.dataset.subregen.split("|");
        regenerateSection(sid, "", card, subId);
      });
    });
    area.appendChild(card);
  });
  renderPaperView();
  fixArticleImages(area);
  renderFormulas(area);
  refreshAiBadges();
}

/* ================= 一键生成全文 ================= */
function setFulltextUI(running) {
  const btn = $("#btnFullText");
  const strip = $("#fulltextStrip");
  btn.disabled = running;
  if (running) {
    btn.innerHTML = '<span class="spinner"></span> 全文生成中…';
    strip.style.display = "block";
  } else {
    btn.innerHTML = "🚀 一键生成全文";
  }
}

async function startFullText(force) {
  if (!state.currentProject) { toast("请先选择项目", "err"); return; }
  if (!state.outline) { toast("请先生成大纲", "err"); return; }
  try {
    const resp = await api("/api/fulltext/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_name: state.currentProject, force }),
    });
    toast(`全文任务已启动：${resp.total} 节（待处理 ${resp.pending}）`, "ok");
    setFulltextUI(true);
    startPolling();
    // 进度面板在右栏工具栏底部，主动滚到可视区避免"看不到进度"
    const _strip = $("#fulltextStrip");
    _strip && _strip.scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (e) {
    toast(`任务启动失败: ${e.message}`, "err");
  }
}

let _ftPrevStates = {}; // 上次各节状态（用于状态变化时的闪光动效）

function renderFulltextStatus(st) {
  const strip = $("#fulltextStrip");
  if (!strip) return;
  const secs = st.sections || [];
  const finished = st.finished || 0;
  const total = st.total || secs.length || 1;
  const running = st.state === "running" || st.state === "queued";

  // 状态徽章 + 呼吸点
  const stripCls = "live";
  strip.classList.toggle(stripCls, running);
  const top = $("#ftTopBadge");
  if (top) {
    if (running) {
      top.style.display = "";
      top.textContent = `🚀 全文 ${finished}/${total} · ${st.total_words || 0} 字`;
    } else { top.style.display = "none"; }
  }

  const badge = $("#ftState");
  const badgeText = running ? (st.state === "queued" ? "排队中" : "生成中")
    : st.state === "done" ? "已完成" : st.state === "cancelled" ? "已取消" : "异常";
  if (badge.textContent !== badgeText) badge.textContent = badgeText;
  const badgeCls = "ft-state-badge " + (running ? "running" : st.state === "done" ? "done" : "err");
  if (badge.className !== badgeCls) badge.className = badgeCls;
  $("#ftPulse").className = "ft-pulse " + (running ? "on" : st.state === "done" ? "done" : "");

  // 计数 / 字数 / 总进度条（平滑动画推进）
  $("#ftCount").textContent = `${finished}/${total} 节`;
  $("#ftWords").textContent = `${st.total_words || 0} 字`;
  $("#ftBarFill").style.width = `${Math.min(100, Math.round((finished / total) * 100))}%`;

  // 分节进度格：✓完成 / 流光进行中 / ✕失败 / 序号待写；状态变化的格子闪光一次
  const flash = new Set();
  secs.forEach((s) => {
    const key = s.id || s.title;
    if (_ftPrevStates[key] !== undefined && _ftPrevStates[key] !== s.state) flash.add(key);
    _ftPrevStates[key] = s.state;
  });
  $("#ftDots").innerHTML = secs.map((s, i) => {
    const cls = s.state === "done" ? (s.repaired ? "repaired" : "done")
      : s.state === "kept" ? "done"
      : s.state === "error" ? "error"
      : (s.state === "running" || st.current === s.id) ? "running" : "todo";
    const mark = (cls === "done" || cls === "repaired") ? "✓" : cls === "error" ? "✕" : String(i + 1);
    const name = esc((s.title || s.id || "").slice(0, 9));
    const score = (cls === "done" || cls === "repaired") && s.ai_score !== undefined && s.ai_score !== null
      ? `<span class="score">AI ${s.ai_score}</span>` : "";
    return `<div class="ftf ${cls}${flash.has(s.id || s.title) ? " flash" : ""}" title="${esc(s.title || s.id || "")}${s.ai_score !== undefined && s.ai_score !== null ? ` · AI味 ${s.ai_score}` : ""}">`
      + `<span class="ftf-mark">${mark}</span><span class="ftf-name">${name}</span>${score}</div>`;
  }).join("");

  // 当前节提示
  const cur = secs.find((s) => s.state === "running" || st.current === s.id);
  $("#ftNow").innerHTML = cur
    ? `<span class="ft-now-spin"></span>正在写「${esc(cur.title || cur.id)}」…`
    : (running ? "准备中…" : "");

  // 事件日志（末 8 条）
  const logEl = $("#ftLog");
  const lines = (st.events || []).slice(-8).map((e) => {
    const tm = (e.time || "").slice(11, 19);
    if (e.type === "section_start") return `<div>[${tm}] <b>开始</b> ${esc(e.title || "")}（目标 ${e.target || "?"} 字）</div>`;
    if (e.type === "review") return `<div>[${tm}] <b>审查</b> ${esc(e.section_id || "")} AI味 ${e.ai_score} → ${e.verdict === "auto_humanize" ? "自动去AI味" : "通过"}</div>`;
    if (e.type === "section_done") return `<div>[${tm}] <b>完成</b> ${esc(e.title || "")} · AI味 ${e.ai_score}${e.repaired ? "（已修复）" : ""} · ${e.seconds}s</div>`;
    if (e.type === "section_error") return `<div>[${tm}] <b style="color:var(--red)">失败</b> ${esc(e.section_id || "")} ${esc(e.error || "")}</div>`;
    if (e.type === "task_start") return `<div>[${tm}] 任务启动：共 ${e.total} 节${e.force ? "（强制重做）" : ""}，体检阈值 ${e.threshold}</div>`;
    if (e.type === "task_end") return `<div>[${tm}] 任务完成</div>`;
    if (e.type === "task_cancelled") return `<div>[${tm}] 任务已取消</div>`;
    if (e.type === "task_error") return `<div>[${tm}] <b style="color:var(--red)">任务异常</b> ${esc(e.error || "")}</div>`;
    return "";
  }).filter(Boolean).join("");
  if (logEl.innerHTML !== lines) {
    logEl.innerHTML = lines;
    logEl.scrollTop = logEl.scrollHeight;
  }

  // 右栏总进度条与字数摘要同步实时刷新（不必等大纲重载）
  const outline = state.outline;
  if (outline) {
    const tt = outline.total_target_words || (outline.sections || []).reduce((a, x) => a + (x.target_words || 0), 0);
    const pct = tt ? Math.min(100, Math.round(((st.total_words || 0) / tt) * 100)) : 0;
    const pf = $("#progressFill");
    if (pf) pf.style.width = `${pct}%`;
    const ws = $("#wordsSummary");
    if (ws) ws.textContent =
      `${outline.template_name || ""} · 已写 ${st.total_words || 0} / 目标 ${tt} 字（${pct}%）`;
  }
}

function startPolling() {
  clearInterval(state.fulltextPoll);
  state.fulltextPoll = setInterval(pollFulltext, 1200);
  pollFulltext();
}

let _lastSectionDone = 0;
let _pollFails = 0;  // 连续轮询失败计数（服务器失联检测）
async function pollFulltext() {
  try {
    const st = await api("/api/fulltext/status");
    if (_pollFails >= 8) toast("与服务器的连接已恢复", "ok");
    _pollFails = 0;
    if (st.state === "none") {
      const wasRunning = $("#fulltextStrip").style.display !== "none";
      clearInterval(state.fulltextPoll);
      setFulltextUI(false);
      $("#fulltextStrip").style.display = "none";
      const _tb = $("#ftTopBadge"); if (_tb) _tb.style.display = "none";
      if (wasRunning) {
        // 进度条还亮着但任务已不存在：服务器重启/中断所致——已完成节已落盘，可续写
        toast("全文任务已中断（服务器连接曾断开或已重启）。已完成章节已保存，请重新点击「一键生成全文」续写未完成节", "err");
        await tryLoadOutline(state.currentProject);
        renderOutline();
      }
      return;
    }
    renderFulltextStatus(st);
    const running = st.state === "running" || st.state === "queued";
    if (!running) {
      clearInterval(state.fulltextPoll);
      setFulltextUI(false);
      // 完成后刷新大纲（各节内容已落盘）
      if (st.state === "done") {
        const doneSecs = (st.sections || []).filter((s) => s.state === "done");
        const repaired = doneSecs.filter((s) => s.repaired).length;
        toast(`全文生成完成：${doneSecs.length} 节、累计 ${st.total_words} 字${repaired ? `、${repaired} 节自动去AI味` : ""}`, "ok");
      } else if (st.state === "cancelled") {
        toast("全文任务已取消（已完成节保留）", "ok");
      } else if (st.state === "error") {
        toast(`全文任务异常：${st.error || "未知错误"}`, "err");
      }
      await tryLoadOutline(state.currentProject);
      renderOutline();
      $("#fulltextStrip").style.display = "none";
      const _tb = $("#ftTopBadge"); if (_tb) _tb.style.display = "none";
      return;
    }
    // 运行中：有节完成就刷新大纲（内容已逐节落盘）
    const doneCount = (st.sections || []).filter((s) => s.state === "done").length;
    if (doneCount !== _lastSectionDone) {
      _lastSectionDone = doneCount;
      await tryLoadOutline(state.currentProject);
      renderOutline();
    }
  } catch (e) {
    // 失联提示：连续失败 ≈20s 后明确告知，避免"无声卡住"的僵尸进度条
    _pollFails++;
    if (_pollFails === 8) {
      $("#ftState").textContent = "连接中断";
      $("#ftState").className = "ft-state-badge err";
      const _fn = $("#ftNow");
      if (_fn) _fn.textContent = "⚠ 与服务器连接中断，重试中…（若长时间未恢复，任务可能已终止）";
    }
  }
}

async function openFullMenu(ev) {
  if (!state.currentProject || !state.outline) { toast("请先选择项目并生成大纲", "err"); return; }
  const secs = state.outline.sections;
  const pending = secs.filter((s) => !(s.status === "drafted" && s.content)).length;
  $("#fullMenuPendingCount").textContent = `${pending}/${secs.length}`;
  $("#fullMenuForceCount").textContent = `${secs.length}`;
  const menu = $("#fullMenu");
  menu.classList.remove("show");
  void menu.offsetWidth;
  menu.classList.add("show");
  // 锚定在一键生成按钮下方
  const anchor = (ev && ev.currentTarget && ev.currentTarget.getBoundingClientRect)
    ? ev.currentTarget : $("#btnFullText");
  const r = anchor ? anchor.getBoundingClientRect() : { left: window.innerWidth / 2, bottom: 120 };
  const mw = menu.offsetWidth || 300, mh = menu.offsetHeight || 180;
  menu.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - mw - 12))}px`;
  menu.style.top = `${Math.max(8, Math.min(r.bottom + 6, window.innerHeight - mh - 12))}px`;
}

function bindFullMenu() {
  document.addEventListener("mousedown", (e) => {
    const menu = $("#fullMenu");
    if (menu && !menu.contains(e.target)) menu.classList.remove("show");
  });
  $("#fullMenu").querySelectorAll(".fm-item").forEach((item) => {
    item.addEventListener("click", () => {
      $("#fullMenu").classList.remove("show");
      startFullText(item.dataset.force === "1");
    });
  });
  $("#btnFullText").addEventListener("click", openFullMenu);
  $("#ftCancel").addEventListener("click", async () => {
    try {
      await api("/api/fulltext/cancel", { method: "POST" });
      toast("已请求取消：将在当前节完成后停止");
    } catch (e) { toast(`取消失败: ${e.message}`, "err"); }
  });
  // 页面加载时恢复进行中任务的状态条
  (async () => {
    try {
      const st = await api("/api/fulltext/status");
      if (st.state === "running" || st.state === "queued") {
        setFulltextUI(true);
        startPolling();
      }
    } catch (e) { /* ignore */ }
  })();
}

/* ================= AI润色 / 去AI味 ================= */
function _badgeText(sec) {
  const ck = state.aiChecks[sec.id];
  if (sec.enhance_scores) {
    return `AI ${sec.enhance_scores.before}→${sec.enhance_scores.after}`;
  }
  if (ck && typeof ck.score === "number") return `AI ${ck.score}`;
  return "AI ?";
}
function _badgeLevel(score) {
  return score >= 50 ? "lv-high" : score >= 30 ? "lv-mid" : "lv-low";
}

async function refreshAiBadges() {
  if (!state.outline) return;
  const drafted = state.outline.sections.filter((s) => s.status === "drafted" && s.content);
  await Promise.all(drafted.map(async (sec) => {
    try {
      const r = await api(`/api/sections/ai_check?project_name=${encodeURIComponent(state.currentProject)}&section_id=${encodeURIComponent(sec.id)}`);
      if (!state.aiChecks[sec.id] || state.aiChecks[sec.id].score !== r.score) {
        state.aiChecks[sec.id] = r;
        const el = document.querySelector(`[data-aicheck="${sec.id}"]`);
        if (el) {
          el.textContent = _badgeText(sec);
          el.classList.remove("lv-low", "lv-mid", "lv-high");
          el.classList.add(_badgeLevel(r.score));
        }
      }
    } catch (e) { /* 徽章静默失败 */ }
  }));
}

async function toggleAiReport(sectionId, card) {
  const box = card.querySelector(`#ai-report-${sectionId}`);
  if (!box) return;
  if (box.style.display !== "none") { box.style.display = "none"; return; }
  box.style.display = "block";
  box.innerHTML = '<span class="spinner"></span> 体检中…';
  try {
    const r = await api(`/api/sections/ai_check?project_name=${encodeURIComponent(state.currentProject)}&section_id=${encodeURIComponent(sectionId)}`);
    state.aiChecks[sectionId] = r;
    const mt = r.metrics || {};
    const markerList = (r.markers || []).map((m) =>
      `<span class="r-chip"> ${esc(m.type)} <b>×${m.count}</b></span>`).join("");
    // 模型级 AIGC 检测（BERT/RoBERTa 第二意见；首次调用会加载模型，稍慢）
    const mc = r.model_check;
    const modelBlock = mc
      ? `<div class="r-model ${mc.verdict}"><b>模型检测</b>：AI 概率 <b>${Math.round(mc.ai_prob * 100)}%</b>`
        + `<span class="r-model-verdict">${mc.verdict === "ai" ? "判定 AI 生成" : mc.verdict === "suspect" ? "疑似（临界）" : "判定人类写作"}</span>`
        + `<span style="opacity:.55">${esc(mc.model)} · ${mc.chunks} 块</span></div>`
      : `<div class="r-model na"><b>模型检测</b>：<span style="opacity:.55">不可用（模型未安装或正文过短），仅显示启发式体检</span></div>`;
    // 朱雀官方 API（腾讯；无 key 时提示如何配置）——判定以 AI 确证占比为硬指标
    const zq = r.zhuque_check;
    const aiP = zq ? Math.round(zq.ai_ratio * 100) : 0;
    const suP = zq ? Math.round(zq.suspect_ratio * 100) : 0;
    const huP = zq ? Math.round(zq.human_ratio * 100) : 0;
    const zqVerdict = !zq ? "na" : (aiP >= 50 ? "ai" : aiP >= 20 ? "suspect" : "human");
    const zhuqueBlock = zq
      ? `<div class="r-zq ${zqVerdict}">
          <div class="rzq-top"><b>朱雀检测</b><span class="r-model-verdict">${zqVerdict === "human" ? "判定人类写作" : zqVerdict === "suspect" ? "疑似（临界）" : "判定 AI 生成"}</span>${zq.tokens ? `<span class="rzq-tok">${zq.tokens} tk</span>` : ""}</div>
          <div class="rzq-stack"><div class="zs-ai" style="width:${Math.max(aiP, aiP > 0 ? 2 : 0)}%">${aiP > 8 ? aiP + "%" : ""}</div><div class="zs-sus" style="width:${Math.max(suP, suP > 0 ? 2 : 0)}%">${suP > 10 ? suP + "%" : ""}</div><div class="zs-hum" style="width:${Math.max(huP, huP > 0 ? 2 : 0)}%">${huP > 12 ? huP + "%" : ""}</div></div>
          <div class="rzq-legend"><span class="zl-ai">AI ${aiP}%</span><span class="zl-sus">疑似 ${suP}%</span><span class="zl-hum">人工 ${huP}%</span></div>
        </div>`
      : (r.zhuque_available === false
        ? `<div class="r-model na"><b>朱雀检测</b>：<span style="opacity:.55">未配置 API Key（顶栏 ⚙ 设置 → 外部服务 填入，月免费 50 万 token）</span></div>`
        : `<div class="r-model na"><b>朱雀检测</b>：<span style="opacity:.55">不可用（正文过短或额度不足）</span></div>`);
    const scoreCls = r.score > 55 ? "ai" : r.score > 35 ? "suspect" : "human";
    box.innerHTML = `
      <div class="r-score ${scoreCls}">
        <div class="rs-num">${r.score}<i>/100</i></div>
        <div class="rs-side"><div class="rs-lab">启发式 AI 味分</div><div class="rs-verdict">${r.score > 55 ? "建议去AI味" : r.score > 35 ? "临界（可优化）" : "自然"}</div></div>
      </div>
      ${zhuqueBlock}
      ${modelBlock}
      <div class="r-chips">${markerList || '<span class="r-chip ok">未命中套话标记</span>'}</div>
      <div class="r-metric">
        <span>句数 <b>${mt.sentence_count ?? "-"}</b></span>
        <span>句均 <b>${mt.mean_len ?? "-"}字</b></span>
        <span>句长标准差 <b>${mt.std_len ?? "-"} </b>&lt;12 过均匀</span>
        <span>短句占比 <b>${mt.short_ratio ?? "-"}</b></span>
        <span>连续等长 <b>${mt.uniform_runs ?? "-"}处</b></span>
        <span>段首连接词 <b>${mt.opener_ratio ?? "-"}</b></span>
        <span>三联排比 <b>${mt.triple_parallel ?? "-"}处</b></span>
      </div>`;
    const el = card.querySelector(`[data-aicheck="${sectionId}"]`);
    if (el) { el.classList.remove("lv-low", "lv-mid", "lv-high"); el.classList.add(_badgeLevel(r.score)); el.textContent = `AI ${r.score}`; }
  } catch (e) {
    box.innerHTML = `体检失败：${esc(e.message)}`;
  }
}

/* 强度选择菜单 */
let _strengthCtx = null;
function showStrengthMenu(x, y, sectionId, mode) {
  _strengthCtx = { sectionId, mode };
  const menu = $("#strengthMenu");
  menu.classList.remove("show");
  void menu.offsetWidth;
  // 坐标兜底：无效坐标(0,0)时定位到视口中心
  if (!x && !y) { x = window.innerWidth / 2 - 110; y = window.innerHeight / 2 - 60; }
  menu.classList.add("show");
  menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - 230))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - 140))}px`;
}
document.addEventListener("mousedown", (e) => {
  const menu = $("#strengthMenu");
  if (menu && !menu.contains(e.target)) menu.classList.remove("show");
});

function bindStrengthMenu() {
  $("#strengthMenu").querySelectorAll(".item").forEach((item) => {
    item.addEventListener("click", () => {
      const { sectionId, mode } = _strengthCtx || {};
      const menuEl = $("#strengthMenu");
      menuEl.classList.remove("show");
      if (!sectionId) return;
      const card = document.querySelector(`.section-card [data-regen="${sectionId}"]`)?.closest(".section-card");
      enhanceSection(sectionId, _strengthCtx.mode, item.dataset.strength, "", card);
    });
  });
}

async function enhanceSection(sectionId, mode, strength, instruction, card) {
  if (!state.currentProject || !state.outline) return;
  if (state.enhancing.has(sectionId)) { toast("该节正在处理中"); return; }
  if (state.editingSid === sectionId) { toast("请先保存或取消该节的编辑，再进行增强", "err"); return; }
  state.enhancing.add(sectionId);
  setGenerating(true);
  const modeName = mode === "humanize" ? "去AI味" : "润色";
  const paperBody = document.querySelector(`[data-paper-content="${sectionId}"]`);
  const paperWrap = document.querySelector(`[data-paper-sid="${sectionId}"]`);
  paperWrap?.classList.add("is-streaming");

  // 流式写进节卡与论文区
  const btn = card?.querySelector(mode === "humanize" ? "[data-humanize]" : "[data-polish]");
  const bodyMd = card?.querySelector(".content-md");
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> 处理中…'; }
  // 明显的过程反馈：状态条挂在节卡顶部（每次调用时按 sectionId 实时查找，
  // 防 renderOutline 重建 DOM 后引用失效）
  const getStatusBar = () => {
    const live = document.querySelector(`.section-card [data-regen="${sectionId}"]`)?.closest(".section-card");
    if (!live) return null;
    let bar = live.querySelector(".enh-status");
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "enh-status";
      live.querySelector(".body")?.prepend(bar);
    }
    return bar;
  };
  const setEnhStatus = (txt, done) => {
    const bar = getStatusBar();
    if (!bar) return;
    bar.innerHTML = (done ? "" : '<span class="ft-now-spin"></span>') + txt;
    bar.classList.toggle("done", !!done);
  };
  setEnhStatus(`${modeName}启动：${strength === "light" ? "轻度" : strength === "deep" ? "深度" : "标准"} · 正在分析文本…`, false);
  if (bodyMd) { card.classList.add("open"); bodyMd.innerHTML = '<span class="spinner"></span>'; }

  let acc = "";
  let doneInfo = "";
  let pendingRender = false;
  const renderTick = setInterval(() => {
    if (!pendingRender) return;
    pendingRender = false;
    if (bodyMd) bodyMd.innerHTML = mdWithFigs(acc);
    if (paperBody) {
      paperBody.innerHTML = mdWithFigs(acc);
      paperBody.closest(".doc-sec-wrap")?.scrollIntoView({ block: "nearest" });
    }
  }, 350);

  try {
    await streamSSE("/api/sections/enhance", {
      project_name: state.currentProject,
      section_id: sectionId,
      mode, strength, instruction,
    }, (ev) => {
      if (ev.type === "chunk") {
        acc += ev.content;
        pendingRender = true;
      } else if (ev.type === "status") {
        // 闭环降AI轮次提示：状态条常驻 + toast
        setEnhStatus(ev.content, false);
        toast(ev.content, "");
      } else if (ev.type === "enhance_done") {
        const sec = state.outline.sections.find((s) => s.id === sectionId);
        if (sec) {
          sec.actual_words = ev.actual_words;
          sec.enhance_scores = { before: ev.before_score, after: ev.after_score };
          state.aiChecks[sectionId] = { score: ev.after_score, markers: ev.markers_after || [] };
        }
        const improved = ev.before_score - ev.after_score;
        doneInfo = `${modeName}完成：AI味 <b>${ev.before_score} → ${ev.after_score}</b>`
          + (improved > 0 ? `（降 ${improved} 分）` : improved < 0 ? "（未改善，已择优回退）" : "（已达标）")
          + ` · ${ev.actual_words}字`;
        toast(`${modeName}完成：AI味 ${ev.before_score} → ${ev.after_score}（${ev.actual_words}字）`,
              ev.after_score < ev.before_score ? "ok" : "");
      } else if (ev.type === "error") {
        throw new Error(ev.content);
      }
    });
    const sec = state.outline.sections.find((s) => s.id === sectionId);
    if (sec) sec.content = acc;
    renderOutline();
    const newCard = document.querySelector(`.section-card [data-regen="${sectionId}"]`)?.closest(".section-card");
    if (newCard) newCard.classList.add("open");
    // 渲染完成后再挂完成状态条（避免被 renderOutline 重建抹掉）
    if (doneInfo) {
      setEnhStatus(doneInfo, true);
      setTimeout(() => { document.querySelector(".enh-status")?.remove(); }, 15000);
    }
  } catch (e) {
    setEnhStatus(`⚠ ${modeName}失败：${esc(e.message)}`, true);
    if (acc && bodyMd) {
      bodyMd.innerHTML = md(acc) + `<div class="placeholder">⚠️ 中途失败：${esc(e.message)}（已保留以上内容）</div>`;
    } else {
      toast(`${modeName}失败: ${e.message}`, "err");
    }
  } finally {
    clearInterval(renderTick);
    if (bodyMd && acc) bodyMd.innerHTML = mdWithFigs(acc);
    if (paperBody && acc) paperBody.innerHTML = mdWithFigs(acc);
    paperWrap?.classList.remove("is-streaming");
    state.enhancing.delete(sectionId);
    setGenerating(false);
    if (btn) { btn.disabled = false; btn.innerHTML = mode === "humanize" ? "🪄 去AI味" : "✨ 润色"; }
  }
}

async function restoreSection(sectionId, card) {
  const ok = await ui.confirm({
    title: "还原章节", icon: "↩",
    message: "还原该节为增强（润色/去AI味）前的原文？",
    confirmText: "还原",
  });
  if (!ok) return;
  try {
    const resp = await api("/api/sections/restore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_name: state.currentProject, section_id: sectionId }),
    });
    const sec = state.outline.sections.find((s) => s.id === sectionId);
    if (sec) {
      sec.content = sec.pre_enhance_content || sec.content;
      sec.actual_words = resp.actual_words;
      delete sec.enhance_scores;
      delete state.aiChecks[sectionId];
    }
    renderOutline();
    const newCard = document.querySelector(`.section-card [data-regen="${sectionId}"]`)?.closest(".section-card");
    if (newCard) newCard.classList.add("open");
    toast(`已还原原文（${resp.actual_words}字）`, "ok");
  } catch (e) {
    toast(`还原失败: ${e.message}`, "err");
  }
}

/* ================= 图片整理工作台 ================= */
function scanPaperFigures() {
  const items = [];
  (state.outline?.sections || []).forEach((sec) => {
    const re = /!\[([^\]]*)\]\(([^)\s]+)\)/g; let m;
    while ((m = re.exec(sec.content || "")) !== null) {
      items.push({ sectionId: sec.id, sectionTitle: sec.title, alt: m[1], path: m[2] });
    }
  });
  return items;
}

async function openFigureWorkshop() {
  if (!state.outline) { toast("请先生成大纲与正文", "err"); return; }
  const modal = document.querySelector("#figWorkshop");
  modal.classList.add("open");
  await renderWorkshopItems();
}

async function renderWorkshopItems() {
  const items = scanPaperFigures();
  const list = document.querySelector("#fwList");
  document.querySelector("#fwTitle").textContent = `🖼 图片整理工作台（${items.length} 张图）`;
  if (!items.length) { list.innerHTML = '<div class="empty-hint">正文中还没有图片引用。重新生成章节时，模型会从图片注册表自动引用；或点正文中的图片位置手动添加。</div>';
    document.querySelector("#fwProgress").textContent = ""; return; }
  list.innerHTML = items.map((it, i) =>
    `<div class="fw-item" data-i="${i}">
      <div class="fw-thumb"><img loading="lazy" src="${rawUrl(it.path)}" data-fwpath="${esc(it.path)}"><div class="fw-missing" style="display:none;">缺失</div></div>
      <div class="fw-info">
        <textarea class="fw-alt" rows="2" data-i="${i}">${esc(it.alt)}</textarea>
        <div class="fw-path" title="${esc(it.path)}">${esc(it.path)}</div>
        <div class="fw-from">来源节：${esc(it.sectionTitle)}</div>
      </div>
      <div class="fw-actions">
        <button class="btn-mini primary" data-fwup="${i}">⬆ 上传更换</button>
        <button class="btn-mini" data-fwpick="${i}">🖼 项目选图</button>
        <button class="btn-mini" data-fwalt="${i}">✏️ 保存题注</button>
        <span class="fw-state" data-state="${i}"></span>
      </div>
    </div>`).join("");
  // 缩略图加载状态 → 就绪/缺失
  const updateFwProgress = () => {
    const missingCount = Array.from(list.querySelectorAll(".fw-state"))
      .filter((el) => el.textContent.startsWith("✗")).length;
    document.querySelector("#fwProgress").textContent = `缺失 ${missingCount} / 共 ${items.length}`;
  };
  list.querySelectorAll(".fw-thumb img").forEach((img, i) => {
    const mark = (ok) => {
      const stateEl = list.querySelector(`[data-state="${i}"]`);
      if (stateEl && !stateEl.textContent) stateEl.textContent = ok ? "✓ 就绪" : "✗ 缺失";
      const miss = list.querySelectorAll(".fw-missing")[i];
      if (miss) miss.style.display = ok ? "none" : "flex";
      updateFwProgress();
    };
    if (img.complete) mark(img.naturalWidth > 0);
    else { img.onload = () => mark(true); img.onerror = () => mark(false); }
  });
  updateFwProgress();
  // 事件
  list.querySelectorAll("[data-fwup]").forEach((b) => b.addEventListener("click", () => {
    const it = items[Number(b.dataset.fwup)];
    workshopUpload(it);
  }));
  list.querySelectorAll("[data-fwpick]").forEach((b) => b.addEventListener("click", () => {
    const it = items[Number(b.dataset.fwpick)];
    window._fwRefresh = renderWorkshopItems;
    openFigurePicker(it.sectionId, it.path);
  }));
  list.querySelectorAll("[data-fwalt]").forEach((b) => b.addEventListener("click", async () => {
    const i = Number(b.dataset.fwalt);
    const it = items[i];
    const newAlt = list.querySelectorAll(".fw-alt")[i].value.trim();
    if (!newAlt) { toast("题注不能为空", "err"); return; }
    const sec = state.outline.sections.find((s) => s.id === it.sectionId);
    if (!sec) return;
    const oldStr = `![${it.alt}](${it.path})`;
    const newStr = `![${newAlt}](${it.path})`;
    if (!sec.content.includes(oldStr)) { toast("未找到原引用", "err"); return; }
    sec.content = sec.content.replace(oldStr, newStr);
    it.alt = newAlt;
    await api("/api/outline/" + encodeURIComponent(state.currentProject), {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ outline: state.outline }) });
    renderOutline();
    toast("题注已保存", "ok");
  }));
}

function workshopUpload(it) {
  let input = document.querySelector("#fwUploadInput");
  if (!input) {
    input = document.createElement("input");
    input.type = "file"; input.id = "fwUploadInput";
    input.accept = ".png,.jpg,.jpeg,.gif,.svg,.webp,.bmp,.tif,.tiff";
    input.style.display = "none"; document.body.appendChild(input);
  }
  input.onchange = async () => {
    const f = input.files[0]; input.value = "";
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    fd.append("project_name", state.currentProject);
    fd.append("section_id", it.sectionId);
    fd.append("old_path", it.path);
    toast("上传替换中…");
    try {
      const resp = await fetch("/api/figures/upload", { method: "POST", headers: authHeaders(), body: fd });
      if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.detail || resp.statusText); }
      await tryLoadOutline(state.currentProject);
      renderOutline();
      await renderWorkshopItems();
      toast("图片已更换：" + f.name, "ok");
    } catch (e) { toast("更换失败: " + e.message, "err"); }
  };
  input.click();
}

/* ================= 换图 ================= */
function openFigureUpload(sectionId, oldPath) {
  let input = document.querySelector("#figUploadInput");
  if (!input) {
    input = document.createElement("input");
    input.type = "file";
    input.id = "figUploadInput";
    input.accept = ".png,.jpg,.jpeg,.gif,.svg,.webp,.bmp,.tif,.tiff";
    input.style.display = "none";
    document.body.appendChild(input);
  }
  input.onchange = async () => {
    const f = input.files[0];
    input.value = "";
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    fd.append("project_name", state.currentProject);
    fd.append("section_id", sectionId);
    fd.append("old_path", oldPath);
    toast("正在上传并替换图片…");
    try {
      const resp = await fetch("/api/figures/upload", { method: "POST", headers: authHeaders(), body: fd });
      if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.detail || resp.statusText); }
      const r = await resp.json();
      await tryLoadOutline(state.currentProject);
      renderOutline();
      if (window._fwRefresh) window._fwRefresh();
      toast("图片已更换：" + f.name, "ok");
    } catch (e) { toast("换图失败: " + e.message, "err"); }
  };
  input.click();
}

async function openFigurePicker(sectionId, oldPath) {
  let modal = document.querySelector("#figPicker");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "figPicker";
    modal.className = "img-modal";
    modal.innerHTML = '<div class="img-modal-bar"><span>🖼 选择项目图片</span><button class="btn-mini" id="figPickClose">✕ 关闭</button></div><div class="fig-grid"></div>';
    document.body.appendChild(modal);
    modal.querySelector("#figPickClose").addEventListener("click", () => modal.classList.remove("open"));
    modal.addEventListener("click", (e) => { if (e.target === modal) modal.classList.remove("open"); });
  }
  const grid = modal.querySelector(".fig-grid");
  grid.innerHTML = '<div class="empty-hint">加载项目图片…</div>';
  modal.classList.add("open");
  try {
    const resp = await api("/api/figures/list?project_name=" + encodeURIComponent(state.currentProject));
    const figs = resp.figures || [];
    grid.innerHTML = figs.map((f, i) =>
      `<div class="fig-cell" data-i="${i}" title="${esc(f.path)}"><img loading="lazy" src="${rawUrl(f.path)}"><div class="fig-cell-name">${esc(f.name)}</div></div>`).join("");
    grid.querySelectorAll(".fig-cell").forEach((cell) => {
      cell.addEventListener("click", async () => {
        const f = figs[Number(cell.dataset.i)];
        try {
          await api("/api/figures/replace?project_name=" + encodeURIComponent(state.currentProject)
            + "&section_id=" + encodeURIComponent(sectionId)
            + "&old_path=" + encodeURIComponent(oldPath)
            + "&new_path=" + encodeURIComponent(f.path), { method: "POST" });
          modal.classList.remove("open");
          await tryLoadOutline(state.currentProject);
          renderOutline();
          if (window._fwRefresh) window._fwRefresh();
          toast("已换为：" + f.name, "ok");
        } catch (e) { toast("换图失败: " + e.message, "err"); }
      });
    });
  } catch (e) { grid.innerHTML = "加载失败: " + esc(e.message); }
}

/* ================= 论文主舞台（中栏实时展示） ================= */
function renderPaperView() {
  const view = $("#paperView");
  const outline = state.outline;
  if (!outline) {
    // 准备向导：无大纲时展示 4 步准备流程（选择项目 → 设置 → 参考 → 理解 → 大纲）
    view.innerHTML = renderPrepWizard();
    bindPrepWizard(view);
    return;
  }
  // 编辑中的节：重绘前摘下编辑器 DOM，重绘后原位放回（textarea 内容/光标不丢）
  const editingWrap = state.editingSid
    ? view.querySelector(`[data-paper-sid="${state.editingSid}"]`)
    : null;
  const editorNode = editingWrap?.querySelector(".sec-editor") || null;
  const totalActual = outline.sections.reduce((s, x) => s + (x.actual_words || 0), 0);
  const totalTarget = outline.total_target_words || outline.sections.reduce((s, x) => s + (x.target_words || 0), 0);
  const pct = totalTarget ? Math.min(100, Math.round((totalActual / totalTarget) * 100)) : 0;
  const doneCount = outline.sections.filter((s) => s.status === "drafted").length;

  const stTitle = outline.paper_title?.trim();
  const tocRows = outline.sections.map((sec, i) => {
    const done = sec.status === "drafted";
    let subs = "";
    (sec.subsections || []).forEach((sub) => {
      const sdone = sub.status === "drafted";
      subs += `<li class="toc-item sub ${sdone ? "done" : "pending"}" data-toc-sub="${esc(sub.title)}">
        <span class="toc-no">·</span>
        <span class="toc-name">${esc(sub.title)}</span>
        <span class="toc-words">${sdone ? (sub.actual_words || 0) + " 字" : "待生成"}</span>
      </li>`;
      (sub.subsections || []).forEach((c3) => {
        const c3done = c3.status === "drafted";
        subs += `<li class="toc-item sub3 ${c3done ? "done" : "pending"}" data-toc-sub="${esc(c3.title)}">
          <span class="toc-no">·</span>
          <span class="toc-name">${esc(c3.title)}</span>
          <span class="toc-words">${c3done ? (c3.actual_words || 0) + " 字" : "待生成"}</span>
        </li>`;
      });
    });
    return `<li class="toc-item ${done ? "done" : "pending"}" data-toc-sid="${sec.id}">
      <span class="toc-no">${i + 1}.</span>
      <span class="toc-name">${esc(sec.title)}</span>
      <span class="toc-words">${done ? (sec.actual_words || 0) + " 字" : "待生成"}</span>
      <button class="toc-edit" data-tocedit="${sec.id}" title="重命名本节">✏️</button>
    </li>${subs}`;
  }).join("");
  let html = `<div class="paper-doc">
    <h1 class="doc-title editable-title" data-edit="paper-title" title="点击修改论文名">${esc(stTitle || "（点击输入论文标题）")}</h1>
    <div class="doc-progress">
      <div class="outline-progress"><div class="fill" style="width:${pct}%"></div></div>
      <span>已写 ${totalActual} / ${totalTarget} 字（${pct}%）· ${doneCount}/${outline.sections.length} 节完成</span>
    </div>
    <nav class="doc-toc">
      <div class="toc-head"><span>目 录</span><span class="toc-hint">点击跳转 · ✏️ 重命名章节</span></div>
      <ol>${tocRows}</ol>
    </nav>`;
  outline.sections.forEach((sec, i) => {
    const done = sec.status === "drafted";
    const editable = done && sec.content && !state.regenerating.has(sec.id) && !state.enhancing.has(sec.id);
    const body = sec.content
      ? mdWithFigs(sec.content)
      : (sec.key_points?.length
          ? `<div class="placeholder">（待生成）本节要点：</div><ul>${sec.key_points.map((k) => `<li>${esc(k)}</li>`).join("")}</ul>`
          : `<div class="placeholder">尚未生成</div>`);
    html += `<div class="doc-sec-wrap ${done ? "" : "is-pending"} ${sec.id === state.editingSid ? "is-editing" : ""}" data-paper-sid="${sec.id}">
      <h2 class="doc-sec"><span>${i + 1}. ${esc(sec.title)}</span>
        <span class="words ${done ? "done" : ""}">${done ? sec.actual_words + " 字" : "目标 " + sec.target_words + " 字"}</span>
        ${editable ? `<button class="sec-edit-btn" data-secedit="${sec.id}" title="编辑本节正文（Markdown 源码，Ctrl+S 保存）">✏️ 编辑</button>` : ""}
      </h2>
      <div class="doc-content" data-paper-content="${sec.id}">${body}</div>
    </div>`;
  });
  html += `</div>`;
  view.innerHTML = html;
  if (editorNode && state.editingSid) {
    const wrap = view.querySelector(`[data-paper-sid="${state.editingSid}"]`);
    if (wrap) {
      const contentEl = wrap.querySelector(".doc-content");
      if (contentEl) contentEl.style.display = "none";
      wrap.appendChild(editorNode);
    } else {
      state.editingSid = null; // 该节已被删除，丢弃编辑器
    }
  }
  fixArticleImages(view);
  renderFormulas(view);
  applyPaperStyle();
}

/* ================= 排版设置（预览 CSS 变量 + 导出查询参数） ================= */
function applyPaperStyle() {
  const st = paperStyle();
  const view = $("#paperView");
  if (!view) return;
  const v = view.style;
  v.setProperty("--doc-fs", `${st.body_font_px}px`);
  v.setProperty("--doc-lh", String(st.line_height));
  v.setProperty("--doc-indent", Number(st.first_indent_em) > 0 ? `${st.first_indent_em}em` : "0");
  v.setProperty("--doc-align", st.body_align === "left" ? "left" : "justify");
  v.setProperty("--doc-table-margin", st.table_align === "left" ? "12px 0" : "12px auto");
  v.setProperty("--doc-table-width", st.table_width === "auto" ? "auto" : "100%");
  v.setProperty("--doc-table-fs", `${st.table_font_px}px`);
  v.setProperty("--doc-cell-align", st.cell_align === "left" ? "left" : "center");
  v.setProperty("--doc-fig-w", `${st.fig_width_pct}%`);
}

function syncStyleMenu() {
  const st = paperStyle();
  document.querySelectorAll("#styleMenu [data-pstyle]").forEach((sel) => {
    const key = sel.dataset.pstyle;
    const val = String(st[key]);
    if ([...sel.options].some((o) => o.value === val)) sel.value = val;
  });
}

function openStyleMenu(anchor) {
  const menu = $("#styleMenu");
  syncStyleMenu();
  menu.classList.remove("show");
  void menu.offsetWidth;
  menu.classList.add("show");
  const r = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : null;
  const x = r ? r.left : window.innerWidth - 280;
  const y = r ? r.bottom + 6 : 120;
  menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - 270))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - menu.offsetHeight - 8))}px`;
}

function bindStyleMenu() {
  const menu = $("#styleMenu");
  document.addEventListener("mousedown", (e) => {
    if (menu.classList.contains("show") && !menu.contains(e.target)) {
      menu.classList.remove("show");
    }
  });
  menu.querySelectorAll("[data-pstyle]").forEach((sel) => {
    sel.addEventListener("change", () => {
      const st = paperStyle();
      const key = sel.dataset.pstyle;
      const numKeys = ["body_font_px", "line_height", "first_indent_em", "table_font_px", "fig_width_pct"];
      st[key] = numKeys.includes(key) ? Number(sel.value) : sel.value;
      applyPaperStyle();
      scheduleSave();
    });
  });
}

/* ================= 论文分节编辑（Markdown 源码） ================= */
function _edWrapSel(ta, before, after = before) {
  const { selectionStart: s, selectionEnd: e, value } = ta;
  const sel = value.slice(s, e) || "文本";
  ta.setRangeText(before + sel + after, s, e, "select");
  ta.focus();
  ta.selectionStart = s + before.length;
  ta.selectionEnd = s + before.length + sel.length;
  ta.dispatchEvent(new Event("input"));
}
function _edLinePrefix(ta, prefix) {
  const { selectionStart: s, value } = ta;
  const ls = value.lastIndexOf("\n", s - 1) + 1;
  ta.setRangeText(prefix, ls, ls, "end");
  ta.focus();
  ta.dispatchEvent(new Event("input"));
}
function _edInsert(ta, text) {
  const s = ta.selectionStart;
  ta.setRangeText(text, s, ta.selectionEnd, "end");
  ta.focus();
  ta.dispatchEvent(new Event("input"));
}
function _edCount(text) {
  return (text || "").replace(/\s/g, "").length;
}

const SEC_EDITOR_TABLE = "\n| 列1 | 列2 | 列3 |\n|:---:|:---:|:---:|\n| 内容 | 内容 | 内容 |\n";

function enterSectionEdit(sectionId) {
  const view = $("#paperView");
  const sec = state.outline?.sections.find((s) => s.id === sectionId);
  if (!view || !sec) return;
  if (state.editingSid === sectionId) return;
  if (state.editingSid) { toast("请先保存或取消正在编辑的节", "err"); return; }
  if (state.regenerating.has(sectionId) || state.enhancing.has(sectionId)) {
    toast("该节正在生成/处理中，暂不能编辑", "err"); return;
  }
  state.editingSid = sectionId;
  const wrap = view.querySelector(`[data-paper-sid="${sectionId}"]`);
  const contentEl = wrap?.querySelector(".doc-content");
  if (!wrap || !contentEl) { state.editingSid = null; return; }
  contentEl.style.display = "none";
  wrap.classList.add("is-editing");

  const editor = document.createElement("div");
  editor.className = "sec-editor";
  editor.innerHTML = `
    <div class="se-toolbar">
      <span class="se-tabs"><button class="se-tab active" data-setab="src">源码</button>
      <button class="se-tab" data-setab="prev">预览</button></span>
      <button type="button" data-secmd="bold" title="粗体"><b>B</b></button>
      <button type="button" data-secmd="italic" title="斜体"><i>I</i></button>
      <button type="button" data-secmd="h3" title="三级标题">H3</button>
      <button type="button" data-secmd="h4" title="四级标题">H4</button>
      <button type="button" data-secmd="table" title="插入表格模板">表格</button>
      <button type="button" data-secmd="quote" title="引用块">引用</button>
      <button type="button" data-secmd="code" title="行内代码">&lt;/&gt;</button>
      <span class="se-hint">图片与公式保持原样即可</span>
    </div>
    <textarea class="se-src" spellcheck="false"></textarea>
    <div class="se-preview doc-content" style="display:none;"></div>
    <div class="se-footer">
      <span class="se-count">约 0 字</span>
      <span class="se-actions">
        <button class="btn-mini" data-secancel title="放弃修改（Esc）">✕ 取消</button>
        <button class="btn-mini primary" data-sesave title="保存到项目（Ctrl+S）">💾 保存</button>
      </span>
    </div>`;
  wrap.appendChild(editor);
  const ta = editor.querySelector(".se-src");
  ta.value = sec.content || "";
  const countEl = editor.querySelector(".se-count");
  const updCount = () => { countEl.textContent = `约 ${_edCount(ta.value)} 字`; };
  updCount();
  ta.addEventListener("input", updCount);

  const commands = {
    bold: () => _edWrapSel(ta, "**"),
    italic: () => _edWrapSel(ta, "*"),
    h3: () => _edLinePrefix(ta, "### "),
    h4: () => _edLinePrefix(ta, "#### "),
    table: () => _edInsert(ta, SEC_EDITOR_TABLE),
    quote: () => _edLinePrefix(ta, "> "),
    code: () => _edWrapSel(ta, "`"),
  };
  editor.querySelectorAll("[data-secmd]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (ta.style.display === "none") return; // 预览页签下禁用
      commands[btn.dataset.secmd]?.();
      updCount();
    });
  });
  editor.querySelectorAll(".se-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      editor.querySelectorAll(".se-tab").forEach((t) => t.classList.toggle("active", t === tab));
      const previewMode = tab.dataset.setab === "prev";
      ta.style.display = previewMode ? "none" : "";
      const prev = editor.querySelector(".se-preview");
      prev.style.display = previewMode ? "" : "none";
      if (previewMode) {
        prev.innerHTML = mdWithFigs(ta.value || "（空）");
        fixArticleImages(prev);
        renderFormulas(prev);
      }
    });
  });
  editor.querySelector("[data-secancel]").addEventListener("click", exitSectionEdit);
  editor.querySelector("[data-sesave]").addEventListener("click", saveSectionEdit);
  ta.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); saveSectionEdit(); }
    else if (e.key === "Escape") { e.preventDefault(); exitSectionEdit(); }
    else if (e.key === "Tab") { e.preventDefault(); _edInsert(ta, "  "); }
  });
  ta.focus();
  wrap.scrollIntoView({ block: "nearest" });
}

function exitSectionEdit() {
  const sid = state.editingSid;
  if (!sid) return;
  state.editingSid = null;
  const view = $("#paperView");
  const wrap = view?.querySelector(`[data-paper-sid="${sid}"]`);
  const editor = wrap?.querySelector(".sec-editor");
  if (wrap && editor) {
    editor.remove();
    const contentEl = wrap.querySelector(".doc-content");
    if (contentEl) contentEl.style.display = "";
    wrap.classList.remove("is-editing");
  }
}

async function saveSectionEdit() {
  const sid = state.editingSid;
  if (!sid || !state.currentProject) return;
  const editor = document.querySelector(`#paperView [data-paper-sid="${sid}"] .sec-editor`);
  const sec = state.outline?.sections.find((s) => s.id === sid);
  if (!editor || !sec) { exitSectionEdit(); return; }
  const content = editor.querySelector(".se-src").value;
  if (content === sec.content) { exitSectionEdit(); return; } // 无改动直接退出
  const saveBtn = editor.querySelector("[data-sesave]");
  saveBtn.disabled = true;
  saveBtn.innerHTML = '<span class="spinner"></span> 保存中…';
  try {
    const resp = await api("/api/sections/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_name: state.currentProject,
        section_id: sid,
        content,
      }),
    });
    sec.content = content;
    sec.actual_words = resp.actual_words ?? _edCount(content);
    if (resp.status) sec.status = resp.status;
    delete state.aiChecks[sid]; // 内容已变，旧 AI 味检测失效
    state.editingSid = null;
    renderOutline(); // 刷新论文区 + 右栏大纲卡（字数/徽章）
    const wrap = document.querySelector(`[data-paper-sid="${sid}"]`);
    if (wrap) wrap.classList.add("reveal");
    toast(`「${sec.title}」已保存：${sec.actual_words} 字`, "ok");
  } catch (e) {
    saveBtn.disabled = false;
    saveBtn.innerHTML = "💾 保存";
    toast(`保存失败: ${e.message}`, "err");
  }
}

/* 论文区事件委托：✏️ 编辑按钮 + 论文名/目录交互（#paperView 常驻，委托一次即可） */
function bindPaperEdit() {
  const view = $("#paperView");
  if (!view) return;
  view.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-secedit]");
    if (btn) { e.preventDefault(); e.stopPropagation(); enterSectionEdit(btn.dataset.secedit); return; }
    const tedit = e.target.closest("[data-tocedit]");
    if (tedit) { e.preventDefault(); e.stopPropagation(); startTocRename(tedit.dataset.tocedit, tedit.closest(".toc-item")); return; }
    if (e.target.closest("[data-edit='paper-title']")) { e.preventDefault(); startTitleEdit(); return; }
    const item = e.target.closest("[data-toc-sid]");
    if (item) {
      const target = view.querySelector(`[data-paper-sid="${item.dataset.tocSid}"]`);
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    const subItem = e.target.closest("[data-toc-sub]");
    if (subItem) {
      // 小节跳转：按标题文本匹配正文 h3/h4 标题（编号由程序注入，取标题尾段匹配）
      const title = subItem.dataset.tocSub;
      const wrap2 = subItem.closest(".paper-doc");
      const heads = wrap2 ? [...wrap2.querySelectorAll(".doc-content h3, .doc-content h4")] : [];
      const el = heads.find((h) => (h.textContent || "").includes(title)) || null;
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
}

/* 论文名行内编辑 */
function startTitleEdit() {
  const el = document.querySelector("#paperView [data-edit='paper-title']");
  if (!el || el.querySelector("input")) return;
  const cur = state.outline?.paper_title?.trim() || "";
  const input = document.createElement("input");
  input.type = "text";
  input.className = "title-input";
  input.value = cur;
  input.placeholder = "输入论文标题";
  el.textContent = "";
  el.appendChild(input);
  input.focus();
  input.select();
  const finish = async (save) => {
    const val = input.value.trim();
    input.replaceWith(document.createTextNode(""));
    if (!save || !val || val === cur) { renderPaperView(); return; }
    try {
      await api("/api/outline/meta", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_name: state.currentProject, paper_title: val }),
      });
      state.outline.paper_title = val;
      toast("论文名已更新", "ok");
    } catch (err) {
      toast(`论文名保存失败: ${err.message}`, "err");
    }
    renderPaperView();
  };
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
    else if (ev.key === "Escape") { ev.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
}

/* 目录章节改名（联动大纲卡与正文标题） */
function startTocRename(sid, row) {
  if (!row || row.querySelector("input")) return;
  const sec = state.outline?.sections.find((s) => s.id === sid);
  if (!sec) return;
  const nameEl = row.querySelector(".toc-name");
  const cur = sec.title || "";
  const input = document.createElement("input");
  input.type = "text";
  input.className = "toc-input";
  input.value = cur;
  nameEl.replaceChildren(input);
  input.focus();
  input.select();
  let finished = false;
  const finish = async (save) => {
    if (finished) return;
    finished = true;
    const val = input.value.trim();
    if (!save || !val || val === cur) { renderPaperView(); return; }
    try {
      await api("/api/sections/rename", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_name: state.currentProject, section_id: sid, title: val }),
      });
      sec.title = val;
      toast(`章节已重命名：${val}`, "ok");
    } catch (err) {
      toast(`重命名失败: ${err.message}`, "err");
    }
    renderOutline(); // 目录 + 正文标题 + 右栏大纲卡一起刷新
  };
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
    else if (ev.key === "Escape") { ev.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
}

async function regenerateSection(sectionId, instruction, card, subsectionId = null) {
  if (state.regenerating.has(sectionId + (subsectionId || ""))) return;
  if (!state.currentProject || !state.outline) return;
  if (!subsectionId && state.editingSid === sectionId) { toast("请先保存或取消该节的编辑，再重新生成", "err"); return; }
  state.regenerating.add(sectionId + (subsectionId || ""));
  card = card || document.querySelector(`.section-card[data-sid="${sectionId}"]`);
  const btn = card?.querySelector("[data-regen]");
  const bodyMd = card?.querySelector(".content-md");
  if (btn && !subsectionId) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> 生成中…'; }
  if (bodyMd) { card.classList.add("open"); bodyMd.innerHTML = '<span class="spinner"></span>'; }

  let acc = "";
  let doneInfo = "";
  let pendingRender = false;
  const paperBody = document.querySelector(`[data-paper-content="${sectionId}"]`);
  const paperWrap = document.querySelector(`[data-paper-sid="${sectionId}"]`);
  // 节流渲染：流式块只累积，每350ms统一重绘一次（长节3000+块时避免O(n²)拖死主线程）
  const renderTick = setInterval(() => {
    if (!pendingRender) return;
    pendingRender = false;
    if (bodyMd) bodyMd.innerHTML = mdWithFigs(acc);
    fixArticleImages(bodyMd);
    if (paperBody) {
      paperBody.innerHTML = mdWithFigs(acc);
      renderFormulas(paperBody);
      paperBody.closest(".doc-sec-wrap")?.scrollIntoView({ block: "nearest" });
    }
  }, 350);
  try {
    await streamSSE("/api/sections/regenerate", {
      project_name: state.currentProject,
      section_id: sectionId,
      instruction,
      extra_materials: "",
      subsection_id: subsectionId || null,
    }, (ev) => {
      if (ev.type === "chunk") {
        acc += ev.content;
        pendingRender = true;
      } else if (ev.type === "section_done") {
        const sec = state.outline.sections.find((s) => s.id === sectionId);
        if (sec) { sec.actual_words = ev.actual_words; sec.status = "drafted"; }
        paperWrap?.classList.remove("is-streaming");
        paperWrap?.classList.add("reveal");
        pendingRender = true; // 终态渲染
        scheduleSave();
        if (card) card.querySelector(".content-md").innerHTML = md(acc);
        toast(`「${sec?.title || sectionId}」完成：${ev.actual_words}/${ev.target_words} 字`, "ok");
      } else if (ev.type === "error") {
        throw new Error(ev.content);
      }
    });
    renderOutline();
    // 重新打开当前卡
    const newCard = document.querySelector(`[data-sid="${sectionId}"]`);
    if (newCard) newCard.classList.add("open");
  } catch (e) {
    if (acc) {
      // 保留已生成的部分，追加失败提示（点"生成中…"按钮可直接重试）
      if (bodyMd) bodyMd.innerHTML = md(acc) + `<div class="placeholder">⚠️ 中途失败：${esc(e.message)}（已保留以上内容，可重新点击生成继续）</div>`;
    } else {
      if (bodyMd) bodyMd.innerHTML = `<div class="placeholder">生成失败：${esc(e.message)}</div>`;
    }
    toast(`分节生成失败: ${e.message}`, "err");
  } finally {
    clearInterval(renderTick);
    // 终态完整渲染一次
    if (bodyMd) { bodyMd.innerHTML = mdWithFigs(acc); fixArticleImages(bodyMd); }
    if (paperBody) { paperBody.innerHTML = mdWithFigs(acc); fixArticleImages(paperBody); renderFormulas(paperBody); }
    state.regenerating.delete(sectionId + (subsectionId || ""));
    setGenerating(false);
    paperWrap?.classList.remove("is-streaming");
  }
}

/* ================= 自查 / 导出 ================= */
async function selfReview() {
  if (!state.currentProject) { toast("请先选择项目", "err"); return; }
  setGenerating(true);
  $("#reviewSlot").innerHTML = '<div class="review-report"><span class="spinner"></span> 模拟审稿进行中…</div>';
  switchWsTab("outline"); // 报告展示在大纲视图底部
  try {
    const resp = await api(`/api/sections/self_review?project_name=${encodeURIComponent(state.currentProject)}`,
      { method: "POST" });
    $("#reviewSlot").innerHTML = `<div class="review-report">${esc(resp.report)}</div>`;
    toast("自查完成：报告已附在大纲页底部", "ok");
  } catch (e) {
    $("#reviewSlot").innerHTML = "";
    toast(`自查失败: ${e.message}`, "err");
  } finally {
    setGenerating(false);
  }
}

/* 导出防抖：同一时刻仅允许一个导出任务（PDF 走本机 Word COM 转换，耗时可达 1 分钟；
   重复点击曾是下载栏堆出 (1)(2)(3)… 一排同名副本的直接原因） */
let exportingFmt = null;

async function exportPaper(fmt) {
  if (!state.currentProject) { toast("请先选择项目", "err"); return; }
  if (exportingFmt) { toast(`正在导出 ${exportingFmt.toUpperCase()}，请等待完成后再点击`, "err"); return; }
  const params = new URLSearchParams({ project_name: state.currentProject });
  // docx 导出按「排版设置」面板的当前值排版（md 无对齐语义，不传）
  if (fmt === "docx" || fmt === "pdf") {
    const st = paperStyle();
    params.set("body_font_px", st.body_font_px);
    params.set("line_height", st.line_height);
    params.set("first_indent_em", Number(st.first_indent_em) > 0 ? st.first_indent_em : 0);
    params.set("body_align", st.body_align);
    params.set("table_align", st.table_align);
    params.set("table_width", st.table_width);
    params.set("table_font_px", st.table_font_px);
    params.set("cell_align", st.cell_align);
    params.set("fig_width_pct", st.fig_width_pct);
  }
  const url = `/api/outline/export/${fmt}?${params.toString()}`;
  exportingFmt = fmt;
  const menuBtn = $("#btnExportMenu");
  if (menuBtn) menuBtn.classList.add("busy");
  toast(fmt === "pdf" ? "🖨 PDF 正在本机 Word 中转换（约 30–60 秒），完成后自动下载…"
    : "正在生成导出文件，请稍候…");
  try {
    const resp = await fetch(url, { headers: authHeaders() });
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try { const j = await resp.json(); if (j.detail) detail = j.detail; } catch (_) { /* 非JSON错误体 */ }
      throw new Error(detail);
    }
    // 下载文件名以服务端为准（论文标题；未设标题时回退项目名+时间戳）
    const disp = resp.headers.get("Content-Disposition") || "";
    let name = "";
    const m = disp.match(/filename\*?=(?:UTF-8'')?"?([^";]+)/i);
    if (m) { try { name = decodeURIComponent(m[1]); } catch (_) { name = m[1]; } }
    if (!name) {
      const title = String(state.outline?.paper_title || "")
        .trim().replace(/[\\/:*?"<>|]/g, "_");
      if (title) {
        name = `${title}.${fmt}`;
      } else {
        const ts = new Date();
        const pad = (n) => String(n).padStart(2, "0");
        const stamp = `${ts.getFullYear()}${pad(ts.getMonth() + 1)}${pad(ts.getDate())}_${pad(ts.getHours())}${pad(ts.getMinutes())}${pad(ts.getSeconds())}`;
        const proj = (state.currentProject || "script").replace(/[\\/:*?"<>|]/g, "_");
        name = `${proj}.论文草稿_${stamp}.${fmt}`;
      }
    }
    const blob = await resp.blob();
    const objUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objUrl;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(objUrl);
    toast(`已导出：${name}`, "ok");
  } catch (e) {
    toast(`导出失败: ${e.message}`, "err");
  } finally {
    exportingFmt = null;
    if (menuBtn) menuBtn.classList.remove("busy");
  }
}

/* ================= 知识库构建 ================= */
async function buildKb(which) {
  const btnId = which === "methods" ? "#btnBuildMethods" : "#btnBuildWriting";
  const btn = $(btnId);
  btn.disabled = true;
  btn.innerHTML = "构建中";
  toast(`开始构建${which === "methods" ? "方法原论文" : "写作教学"}知识库（可能需要几分钟）…`);
  try {
    const resp = await api(`/api/kb/build/${which}`, { method: "POST" });
    const kb = resp.kb || {};
    $("#kbMethods").textContent = `${kb.method_papers?.document_count ?? 0} 块`;
    $("#kbWriting").textContent = `${kb.writing_methodology?.document_count ?? 0} 块`;
    toast("知识库构建完成", "ok");
  } catch (e) {
    toast(`知识库构建失败: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
    btn.innerHTML = "更新";
  }
}

/* ================= 画像深化（重扫项目 + 视觉逐图分析） ================= */
/* ================= 项目理解向导（理解 Agent：扫描→分析→弹窗澄清→研究主体） ================= */
const UW = { modal: null, timer: null, project: null, questions: [], raw: "" };

function ensureUwModal() {
  if (UW.modal) return UW.modal;
  const modal = document.createElement("div");
  modal.id = "understandWizard";
  modal.className = "uw-overlay";
  modal.innerHTML =
    '<div class="uw-dialog">'
    + '<div class="uw-head"><div class="uw-head-main">'
    + '<div class="uw-head-title">🧠 项目理解</div>'
    + '<div class="uw-head-sub">理清研究顺序、核心脚本与结果图表 → 生成《研究项目主体》注入全部写作任务</div></div>'
    + '<button class="btn-mini" id="uwClose">✕</button></div>'
    + '<div class="uw-content" id="uwContent"></div>'
    + '<div class="uw-foot" id="uwFoot"></div>'
    + "</div>";
  document.body.appendChild(modal);
  modal.querySelector("#uwClose").addEventListener("click", closeUwModal);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeUwModal(); });
  // 边缘拖拽拉伸：右缘(E) / 下缘(S) / 右下角(SE)，双击手柄复位默认尺寸
  const dlg = modal.querySelector(".uw-dialog");
  ["e", "s", "se"].forEach((dir) => {
    const h = document.createElement("div");
    h.className = "uw-rz uw-rz-" + dir;
    h.title = "拖拽拉伸 · 双击复位";
    h.addEventListener("mousedown", (e) => uwResizeStart(e, dlg, dir));
    h.addEventListener("dblclick", () => { dlg.style.width = ""; dlg.style.height = ""; });
    dlg.appendChild(h);
  });
  UW.modal = modal;
  return modal;
}

function uwResizeStart(e, dlg, dir) {
  e.preventDefault(); e.stopPropagation();
  const sx = e.clientX, sy = e.clientY;
  const sw = dlg.offsetWidth, sh = dlg.offsetHeight;
  const minW = 560, minH = 420;
  const maxW = Math.round(window.innerWidth * 0.96), maxH = Math.round(window.innerHeight * 0.94);
  const move = (ev) => {
    if (dir !== "s") dlg.style.width = Math.min(maxW, Math.max(minW, sw + ev.clientX - sx)) + "px";
    if (dir !== "e") dlg.style.height = Math.min(maxH, Math.max(minH, sh + ev.clientY - sy)) + "px";
  };
  const up = () => { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); };
  document.addEventListener("mousemove", move);
  document.addEventListener("mouseup", up);
}
function closeUwModal() {
  if (UW.timer) { clearInterval(UW.timer); UW.timer = null; }
  if (UW.modal) UW.modal.classList.remove("open");
}
function uwBusy(label, hint) {
  UW.modal.querySelector("#uwContent").innerHTML =
    '<div class="uw-center"><div class="spinner" style="width:36px;height:36px;"></div>'
    + '<div class="uw-title">' + label + "</div>"
    + (hint ? '<div class="uw-hint">' + hint + "</div>" : "") + "</div>";
  UW.modal.querySelector("#uwFoot").innerHTML = "";
}

async function openUnderstandWizard(projectName, projectPath, force = false) {
  ensureUwModal();
  UW.project = projectName;
  UW.modal.classList.add("open");
  if (!force) {
    try {
      const cur = await api("/api/project/body?project_name=" + encodeURIComponent(projectName));
      if (cur.exists && cur.body) { uwShowBody(cur.body); return; }
    } catch (e) { /* 落到重新理解 */ }
  }
  uwBusy("正在通读项目…", "目录骨架 / 脚本头部 / README 自述 / 结果与图表清单 —— 大模型将判断哪些脚本核心、哪些可弃，并整理研究顺序（约 1-3 分钟）");
  try {
    await api("/api/project/understand/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: projectName, path: projectPath || projectRootPath() || "" }),
    });
  } catch (e) {
    UW.modal.querySelector("#uwContent").innerHTML =
      '<div class="uw-err">启动失败: ' + esc(e.message) + "</div>";
    return;
  }
  UW.timer = setInterval(uwPollStatus, 2500);
  uwPollStatus();
}

async function uwPollStatus() {
  if (!UW.project || !UW.modal.classList.contains("open")) return;
  let st;
  try {
    st = await api("/api/project/understand/status?project_name=" + encodeURIComponent(UW.project));
  } catch (e) { return; }
  if (st.state === "analyzing") return;
  if (st.state === "finalizing") {
    const c = UW.modal.querySelector("#uwContent");
    if (!c.querySelector(".uw-finalizing")) {
      uwBusy("正在结合你的回答撰写《研究项目主体》…", "");
      c.firstElementChild.classList.add("uw-finalizing");
    }
    return;
  }
  clearInterval(UW.timer); UW.timer = null;
  if (st.state === "awaiting_answers" && (st.questions || []).length) {
    uwShowQuestions(st.questions);
  } else if (st.state === "analyzed" || st.state === "done") {
    const cur = await api("/api/project/body?project_name=" + encodeURIComponent(UW.project));
    if (cur.exists && cur.body) uwShowBody(cur.body);
    else uwShowQuestions(st.questions || []);
  } else if (st.state === "failed") {
    UW.modal.querySelector("#uwContent").innerHTML =
      '<div class="uw-err">理解失败：' + esc(st.error || "未知错误") + "</div>";
    UW.modal.querySelector("#uwFoot").innerHTML = '<button class="btn" id="uwRetry">🔄 重试</button>';
    UW.modal.querySelector("#uwRetry").addEventListener("click", () => openUnderstandWizard(UW.project, null, true));
  }
}

function uwShowQuestions(questions) {
  UW.questions = questions || [];
  const c = UW.modal.querySelector("#uwContent");
  const items = UW.questions.map((q, i) => {
    let input;
    if (q.type === "text") {
      input = '<textarea class="uw-text" id="uwq_' + i + '" placeholder="补充说明（可留空）"></textarea>';
    } else {
      const opts = (q.options || []).map((o, j) =>
        '<label class="uw-opt"><input type="' + (q.type === "multi" ? "checkbox" : "radio")
        + '" name="uwq_' + i + '" value="' + esc(o) + '" '
        + ((q.type !== "multi" && j === 0) ? "checked" : "") + '><span>' + esc(o) + "</span></label>").join("");
      input = '<div class="uw-opts">' + opts + "</div>";
    }
    return '<div class="uw-q"><div class="uw-q-title"><span class="uw-q-no">' + (i + 1) + "</span>" + esc(q.title || "") + "</div>"
      + '<div class="uw-q-detail">' + esc(q.detail || "") + "</div>" + input + "</div>";
  }).join("");
  c.innerHTML =
    '<div class="uw-head-note">项目已通读完毕。以下 <b>' + UW.questions.length
    + "</b> 个模糊处需要你拍板，回答后即生成《研究项目主体》：</div>"
    + (items || '<div class="uw-head-note">无待确认项。</div>');
  UW.modal.querySelector("#uwFoot").innerHTML =
    '<button class="btn" id="uwSkip">跳过（按推测生成）</button>'
    + '<button class="btn primary" id="uwSubmit">✅ 提交并生成研究主体</button>';
  const submit = async () => {
    const answers = UW.questions.map((q, i) => {
      if (q.type === "text") {
        const ta = document.querySelector("#uwq_" + i);
        return { id: q.id, text: (ta ? ta.value.trim() : "") };
      }
      const chosen = Array.from(document.querySelectorAll('input[name="uwq_' + i + '"]:checked')).map((x) => x.value);
      return { id: q.id, choices: chosen };
    });
    uwBusy("正在合成《研究项目主体》…", "");
    UW.modal.querySelector("#uwContent .uw-center").classList.add("uw-finalizing");
    try {
      await api("/api/project/understand/answers", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_name: UW.project, answers: answers }),
      });
      UW.timer = setInterval(uwPollStatus, 2500);
    } catch (e) {
      toast("提交失败: " + e.message, "err");
    }
  };
  UW.modal.querySelector("#uwSubmit").addEventListener("click", submit);
  UW.modal.querySelector("#uwSkip").addEventListener("click", submit);
}

/* ---- 主体展示：markdown 分节渲染 + 锚点导航 ---- */
function uwSplitSections(text) {
  const chunks = text.split(/(?=^## .+)/m).filter((s) => s.trim());
  const secs = [];
  for (const ch of chunks) {
    const m = ch.match(/^##\s*(.+?)\s*$/m);
    secs.push({ title: m ? m[1].replace(/^([一二三四五六七八九十]+、|附录[AB]?[:：]?)/, "").trim() || m[1] : "概述",
      raw: ch.trim(), heading: m ? m[1] : "" });
  }
  return secs;
}

function uwShowBody(text) {
  UW.raw = text;
  state.projectUnderstood = true;  // 研究主体已生成 → 准备向导步骤③完成
  renderPaperView();
  const c = UW.modal.querySelector("#uwContent");
  const secs = uwSplitSections(text);
  const hero = secs.length && !secs[0].heading ? secs.shift() : null;
  const heroRest = hero ? hero.raw.replace(/^#\s+.+\s*$/m, "").trim() : "";
  const heroTitle = hero ? (hero.raw.match(/^#\s*(.+?)\s*$/m) || [])[1] || "" : "";
  const nav = secs.map((s, i) =>
    '<button class="uw-pill" data-i="' + i + '">' + esc(s.title.slice(0, 12)) + "</button>").join("");
  const bodyHtml = secs.map((s, i) => {
    const inner = md(s.raw.replace(/^##\s+.+\s*$/m, "").trim()
      .replace(/^[\s。．.、，；:：]+$/gm, ""));
    return '<section class="uw-sec" id="uwsec_' + i + '">'
      + '<h3 class="uw-sec-title"><span class="uw-sec-bar"></span>' + esc(s.title) + "</h3>"
      + '<div class="uw-sec-body">' + inner + "</div></section>";
  }).join("");
  c.innerHTML =
    (hero ? '<div class="uw-hero"><div class="uw-hero-title">📄 ' + esc(heroTitle) + "</div>"
      + (heroRest ? md(heroRest) : "") + "</div>" : "")
    + '<div class="uw-nav">' + nav + "</div>"
    + '<div class="uw-doc">' + bodyHtml + "</div>";
  // 路径 chip：完整路径进 title 悬停可见，展示层省略号收窄
  c.querySelectorAll(".uw-sec-body code, .uw-hero code").forEach((cd) => { cd.title = cd.textContent; });
  // 短单元格（图/表/指标/阶段N 等枚举值）禁止换行并居中，避免"指/标"竖排
  c.querySelectorAll(".uw-sec-body td").forEach((td) => {
    const txt = td.textContent.trim();
    if (txt.length <= 3 && !td.querySelector("code")) td.classList.add("uw-td-nw");
  });
  // 关键结果数字 → 紧凑网格；核心脚本/结果清单表 → 加类
  c.querySelectorAll(".uw-sec").forEach((sec) => {
    const t = sec.querySelector(".uw-sec-title").textContent || "";
    if (t.includes("关键结果数字")) sec.classList.add("uw-kv");
    if (sec.querySelector("table")) sec.classList.add("uw-has-table");
  });
  c.querySelectorAll(".uw-pill").forEach((p) => p.addEventListener("click", () => {
    const target = c.querySelector("#uwsec_" + p.dataset.i);
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    c.querySelectorAll(".uw-pill").forEach((x) => x.classList.remove("active"));
    p.classList.add("active");
  }));
  UW.modal.querySelector("#uwFoot").innerHTML =
    '<span class="uw-foot-note">已注入大纲生成 · 正文写作 · 项目问答（不向量化，随提示词下发）</span>'
    + '<button class="btn" id="uwEdit">✏️ 编辑</button>'
    + '<button class="btn" id="uwRerun">🔄 重新理解</button>'
    + '<button class="btn primary" id="uwOk">完成</button>';
  UW.modal.querySelector("#uwOk").addEventListener("click", closeUwModal);
  UW.modal.querySelector("#uwRerun").addEventListener("click", () => openUnderstandWizard(UW.project, null, true));
  UW.modal.querySelector("#uwEdit").addEventListener("click", () => uwEditMode());
}

function uwEditMode() {
  const c = UW.modal.querySelector("#uwContent");
  c.innerHTML = '<textarea class="uw-edit-ta" id="uwTa"></textarea>';
  const ta = c.querySelector("#uwTa");
  ta.value = UW.raw;
  UW.modal.querySelector("#uwFoot").innerHTML =
    '<button class="btn" id="uwCancel">取消</button>'
    + '<button class="btn primary" id="uwSave">💾 保存</button>';
  UW.modal.querySelector("#uwCancel").addEventListener("click", () => uwShowBody(UW.raw));
  UW.modal.querySelector("#uwSave").addEventListener("click", async () => {
    try {
      await api("/api/project/body", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_name: UW.project, body: ta.value }),
      });
      toast("研究主体已保存", "ok");
      uwShowBody(ta.value);
    } catch (e) { toast("保存失败: " + e.message, "err"); }
  });
}

async function understandProject(force = false) {
  if (!state.currentProject) { toast("请先选择项目", "err"); return; }
  await openUnderstandWizard(state.currentProject, projectRootPath(), force);
}

async function enrichProfile() {
  if (!state.currentProject) { toast("请先选择项目", "err"); return; }
  const btn = $("#btnEnrichProfile");
  btn.disabled = true;
  const path = projectRootPath();
  try {
    // 1) 重扫项目（新版索引器会读取 results/output 的结果文件与结果表）
    if (path) {
      btn.innerHTML = "🔄 重扫项目文件…";
      await api("/api/projects/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: state.currentProject, path,
          domain: ($("#domainSelect")?.value === "auto") ? null : ($("#domainSelect")?.value || null),
        }),
      });
    }
    // 2) 视觉逐图分析（后台线程）
    btn.innerHTML = "🔬 视觉分析图片…";
    const resp = await api(`/api/profile/enrich?project_name=${encodeURIComponent(state.currentProject)}`,
      { method: "POST" });
    if (resp.state !== "running") {
      toast(resp.message || "画像已是最新", "ok");
      btn.innerHTML = "🔬 深化画像";
      btn.disabled = false;
      return;
    }
    const timer = setInterval(async () => {
      try {
        const st = await api("/api/profile/enrich/status");
        btn.innerHTML = `🔬 看图中 ${st.done}/${st.total}…`;
        if (st.state !== "running") {
          clearInterval(timer);
          btn.innerHTML = "🔬 深化画像";
          btn.disabled = false;
          toast(`画像深化完成：${st.done} 张图片已生成内容描述。建议重新「生成大纲」让新证据进入 beats`, "ok");
          await loadProjects();
        }
      } catch (e) { /* 轮询失败静默 */ }
    }, 3000);
  } catch (e) {
    btn.innerHTML = "🔬 深化画像";
    btn.disabled = false;
    toast(`画像深化失败: ${e.message}`, "err");
  }
}

/* ================= 认证事件绑定 ================= */
function bindAuthEvents() {
  $("#btnLoginSubmit")?.addEventListener("click", doLogin);
  $("#btnRegSubmit")?.addEventListener("click", doRegister);
  $("#loginPass")?.addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
  $("#regPass")?.addEventListener("keydown", (e) => { if (e.key === "Enter") doRegister(); });
  $("#authToReg")?.addEventListener("click", () => {
    $("#authLoginBox").style.display = "none";
    $("#authRegBox").style.display = "";
    $("#regUser") && $("#regUser").focus();
  });
  $("#authToLogin")?.addEventListener("click", () => {
    $("#authRegBox").style.display = "none";
    $("#authLoginBox").style.display = "";
  });
  $("#adminModalClose")?.addEventListener("click", () => $("#adminModal").classList.remove("open"));
  $("#btnAdminCreate")?.addEventListener("click", adminCreateUser);
}

/* ================= 静态事件绑定 ================= */
function bindStaticEvents() {
  $("#btnRefreshProjects").addEventListener("click", loadProjects);
  $("#btnSetRoot").addEventListener("click", setWorkspaceRoot);
  $("#refSelect").addEventListener("change", () => {
    scheduleSave(); syncRefSelectTitle(); loadTemplatePanel();
    renderPaperView();  // 参考论文状态变化 → 准备向导步骤②同步
  });
  $("#btnTplPreview").addEventListener("click", openTplPreview);
  $("#btnSkelPreview").addEventListener("click", openSkelPreview);
  $("#btnSkelExtract").addEventListener("click", extractSkeleton);
  $("#btnTplApply").addEventListener("click", applyTemplate);
  $("#btnTplIgnore").addEventListener("click", ignoreTemplate);
  $("#totalWords").addEventListener("input", scheduleSave);
  $("#wordsMinus").addEventListener("click", () => stepWords(-5000));
  $("#wordsPlus").addEventListener("click", () => stepWords(5000));
  // 关闭/隐藏页面前兜底保存
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") saveWorkspaceNow(true);
  });
  window.addEventListener("beforeunload", () => saveWorkspaceNow(true));
  $("#btnGenerateOutline").addEventListener("click", generateOutline);
  $("#btnUnderstand").addEventListener("click", () => understandProject(false));
  $("#btnEnrichProfile").addEventListener("click", enrichProfile);
  $("#fwClose").addEventListener("click", () => document.querySelector("#figWorkshop").classList.remove("open"));
  $("#btnUploadRef").addEventListener("click", () => $("#refFileInput").click());
  $("#refFileInput").addEventListener("change", (ev) => {
    const f = ev.target.files[0];
    if (f) uploadReference(f);
    ev.target.value = "";
  });
  $("#btnBuildMethods").addEventListener("click", () => buildKb("methods"));
  $("#btnBuildWriting").addEventListener("click", () => buildKb("writing"));
  $("#btnBuildRag").addEventListener("click", async () => {
    try {
      const resp = await api("/api/rag/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (resp.ok) {
        toast("多模态文献入库已开始（后台执行，点「状态」查看进度）", "ok");
        loadRagKbCount();
      } else {
        toast(resp.detail || "已有批量任务在运行", "err");
      }
    } catch (e) { toast(`多模态文献入库失败: ${e.message}`, "err"); }
  });
  const loadRagKbCount = async () => {
    try {
      const st = await api("/api/rag/status");
      const el = $("#kbRagDocs");
      if (!el) return;
      if (!st.enabled) { el.textContent = "未启用"; return; }
      if (!st.ready && (st.import_error || st.init_error)) { el.textContent = "引擎不可用"; return; }
      const n = (st.documents || []).length;
      el.textContent = st.job?.running ? `入库中…` : `${n} 篇`;
    } catch (e) { /* 静默降级 */ }
  };
  $("#btnKbStatus")?.addEventListener("click", async () => {
    try {
      const kb = await api("/api/kb/status");
      $("#kbMethods").textContent = `${kb.method_papers?.document_count ?? 0} 块`;
      $("#kbWriting").textContent = `${kb.writing_methodology?.document_count ?? 0} 块`;
      const ragSt = await api("/api/rag/status");
      const n = (ragSt.documents || []).length;
      const ragEl = $("#kbRagDocs");
      if (ragEl) ragEl.textContent = ragSt.job?.running ? `入库中…` : `${n} 篇`;
      toast(`写作库 ${kb.writing_methodology?.document_count ?? 0} 块 | 方法库 ${kb.method_papers?.document_count ?? 0} 块 | 多模态文献 ${n} 篇`);
    } catch (e) { toast("状态获取失败", "err"); }
  });
  loadRagKbCount();
  $("#btnClearSession").addEventListener("click", async () => {
    const sess = activeSession();
    if (!sess || (!sess.messages.length && !sess.attachments.length)) { toast("当前会话已经是空的"); return; }
    const ok = await ui.confirm({
      title: "清空会话", icon: "🧹",
      message: `清空「${sess.name}」的全部消息与附件？（共 ${sess.messages.length} 条）`,
      danger: true, confirmText: "清空",
    });
    if (!ok) return;
    sess.messages = [];
    sess.attachments = [];
    renderChat();
    scheduleSave();
    toast("会话已清空（后端上下文同步重置）", "ok");
  });
  $("#btnSend").addEventListener("click", sendMessage);
  $("#chatInput").addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" || ev.isComposing) return; // 输入法组合期间不拦截
    const mode = loadUiPrefs().sendKey || "ctrl-enter";
    if (mode === "enter") {
      if (!ev.shiftKey && !ev.ctrlKey && !ev.metaKey && !ev.altKey) { ev.preventDefault(); sendMessage(); }
    } else if (ev.ctrlKey || ev.metaKey) {
      ev.preventDefault(); sendMessage();
    }
  });
  $("#chatInput").addEventListener("paste", (ev) => {
    ev.preventDefault();
    const text = (ev.clipboardData || window.clipboardData).getData("text/plain");
    document.execCommand("insertText", false, text);
  });
}

document.addEventListener("DOMContentLoaded", init);
