/* ui.js — 统一交互组件：弹层栈（Esc 关最上层）/ 样式化对话框 / 锚定下拉菜单
   在 app.js 之前加载；全部挂在全局 `ui` 命名空间，零依赖。 */
"use strict";

const ui = (() => {

  /* ================= 弹层栈 =================
     按注册顺序定优先级：后注册的先被 Esc 关闭（菜单 > 弹窗 > 抽屉）。
     每项 { name, isOpen(), close() }，close 由调用方提供（移除 open class / 隐藏）。 */
  const layers = [];
  function layer(name, isOpen, close) {
    if (!name || typeof isOpen !== "function") return () => {};
    layers.push({ name, isOpen, close: typeof close === "function" ? close : () => {} });
    // 返回注销函数
    return () => {
      const i = layers.findIndex((l) => l.name === name);
      if (i >= 0) layers.splice(i, 1);
    };
  }
  function closeTopLayer() {
    for (let i = layers.length - 1; i >= 0; i--) {
      if (layers[i].isOpen()) { layers[i].close(); return true; }
    }
    return false;
  }
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (closeTopLayer()) { e.preventDefault(); e.stopPropagation(); }
  }, true); // 捕获阶段：先于业务各自的 Esc 逻辑；关闭弹层后不再触发下层元素自己的 Esc

  /* ================= 样式化对话框 =================
     ui.confirm({title, message, html, danger, confirmText, cancelText, requireText})
       -> Promise<boolean>   Esc/遮罩/取消 = false
     ui.prompt({title, label, value, placeholder, inputType, confirmText})
       -> Promise<string|null>
     ui.dialog({title, message, html, icon, actions:[{label, value, kind}]})
       -> Promise<value>     kind: primary | danger | ghost */
  function _dialogShell({ title, icon, html, message, width = 420 }) {
    const ov = document.createElement("div");
    ov.className = "ui-dlg-overlay";
    const dlg = document.createElement("div");
    dlg.className = "ui-dlg";
    if (width) dlg.style.width = `min(${width}px, calc(100vw - 48px))`;
    dlg.innerHTML =
      `<div class="ui-dlg-title">${icon ? `<span class="ui-dlg-ico">${icon}</span>` : ""}${title || ""}</div>`
      + `<div class="ui-dlg-body">${html || (message ? `<p>${message}</p>` : "")}</div>`
      + `<div class="ui-dlg-actions"></div>`;
    ov.appendChild(dlg);
    document.body.appendChild(ov);
    requestAnimationFrame(() => ov.classList.add("show"));
    const unbind = layer("ui-dlg:" + (title || ""), () => ov.classList.contains("show"), () => _finish(null));
    let settled = false;
    function _finish(value) {
      if (settled) return;
      settled = true;
      unbind();
      ov.classList.remove("show");
      setTimeout(() => ov.remove(), 180);
      resolver(value);
    }
    let resolver = () => {};
    const promise = new Promise((res) => { resolver = res; });
    ov.addEventListener("mousedown", (e) => { if (e.target === ov) _finish(null); });
    return { ov, dlg, actions: dlg.querySelector(".ui-dlg-actions"), finish: _finish, promise };
  }

  function dialog({ title, message, html, icon, actions = [], width }) {
    const d = _dialogShell({ title, message, html, icon, width });
    actions.forEach((a) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ui-btn " + (a.kind === "danger" ? "ui-btn-danger" : a.kind === "primary" ? "ui-btn-primary" : "ui-btn-ghost");
      btn.textContent = a.label;
      btn.addEventListener("click", () => d.finish(a.value));
      d.actions.appendChild(btn);
    });
    (d.actions.querySelector(".ui-btn-primary, .ui-btn-danger") || d.actions.lastElementChild)?.focus();
    return d.promise;
  }

  function confirm_({ title = "确认操作", message, html, icon, danger = false,
                      confirmText = "确定", cancelText = "取消", requireText = "" } = {}) {
    const d = _dialogShell({ title, icon, message, html, width: 420 });
    let verify = null;
    if (requireText) {
      verify = document.createElement("div");
      verify.className = "ui-dlg-verify";
      verify.innerHTML =
        `<div class="ui-dlg-verify-hint">输入 <b>${requireText}</b> 以确认操作</div>`
        + `<input type="text" class="ui-dlg-verify-input" autocomplete="off" spellcheck="false">`;
      d.dlg.querySelector(".ui-dlg-body").appendChild(verify);
    }
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "ui-btn ui-btn-ghost";
    cancel.textContent = cancelText;
    cancel.addEventListener("click", () => d.finish(false));
    const ok = document.createElement("button");
    ok.type = "button";
    ok.className = "ui-btn " + (danger ? "ui-btn-danger" : "ui-btn-primary");
    ok.textContent = confirmText;
    ok.addEventListener("click", () => d.finish(true));
    if (requireText) {
      ok.disabled = true;
      const input = verify.querySelector("input");
      input.addEventListener("input", () => { ok.disabled = input.value.trim() !== requireText; });
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !ok.disabled) d.finish(true);
      });
      setTimeout(() => input.focus(), 60);
    } else {
      setTimeout(() => ok.focus(), 60);
    }
    d.actions.append(cancel, ok);
    return d.promise;
  }

  function prompt_({ title = "输入", label, value = "", placeholder = "", inputType = "text",
                     confirmText = "确定", cancelText = "取消" } = {}) {
    const d = _dialogShell({ title, width: 400 });
    const box = document.createElement("div");
    box.className = "ui-dlg-field";
    if (label) box.innerHTML += `<label>${label}</label>`;
    const input = document.createElement("input");
    input.type = inputType;
    input.value = value;
    input.placeholder = placeholder;
    input.autocomplete = "off";
    input.className = "ui-dlg-input";
    box.appendChild(input);
    d.dlg.querySelector(".ui-dlg-body").appendChild(box);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); d.finish(input.value); }
    });
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "ui-btn ui-btn-ghost";
    cancel.textContent = cancelText;
    cancel.addEventListener("click", () => d.finish(null));
    const ok = document.createElement("button");
    ok.type = "button";
    ok.className = "ui-btn ui-btn-primary";
    ok.textContent = confirmText;
    ok.addEventListener("click", () => d.finish(input.value));
    d.actions.append(cancel, ok);
    setTimeout(() => { input.focus(); input.select(); }, 60);
    return d.promise;
  }

  /* ================= 锚定下拉菜单 =================
     ui.menu(anchorEl, items) -> Promise<value|null>
     items: [{label, icon, value, danger, disabled, sep, hint}]  sep=分隔线
     自动定位在锚点下方（空间不足时向上），点外部/Esc/选择后关闭。 */
  let _openMenu = null;
  function _closeMenu() {
    if (!_openMenu) return;
    const m = _openMenu;
    _openMenu = null;
    m.el.remove();
    document.removeEventListener("pointerdown", m.outside, true);
    window.removeEventListener("resize", _closeMenu, { passive: true });
    if (m.unbind) m.unbind();
    m.resolve(null);
  }
  function menu(anchor, items = []) {
    if (_openMenu) _closeMenu();
    return new Promise((resolve) => {
      const el = document.createElement("div");
      el.className = "ui-menu";
      el.setAttribute("role", "menu");
      const teardown = () => {
        if (_openMenu && _openMenu.el === el) {
          _openMenu = null;
          document.removeEventListener("pointerdown", outside, true);
          window.removeEventListener("resize", _closeMenu, { passive: true });
          if (unbind) unbind();
        }
        el.remove();
      };
      let unbind = null;
      items.forEach((it) => {
        if (it.sep) {
          const s = document.createElement("div");
          s.className = "ui-menu-sep";
          el.appendChild(s);
          return;
        }
        const item = document.createElement("div");
        item.className = "ui-menu-item" + (it.danger ? " danger" : "") + (it.disabled ? " disabled" : "");
        item.setAttribute("role", "menuitem");
        item.innerHTML =
          (it.icon ? `<span class="ui-menu-ico">${it.icon}</span>` : "")
          + `<span class="ui-menu-txt">${it.label}</span>`
          + (it.hint ? `<span class="ui-menu-hint">${it.hint}</span>` : "");
        if (!it.disabled) {
          item.addEventListener("click", () => {
            teardown();
            resolve(it.value);
          });
        }
        el.appendChild(item);
      });
      document.body.appendChild(el);
      const r = anchor.getBoundingClientRect();
      el.style.minWidth = Math.max(r.width, 150) + "px";
      const mw = el.offsetWidth, mh = el.offsetHeight;
      let left = Math.max(8, Math.min(r.left, window.innerWidth - mw - 8));
      let top = r.bottom + 6;
      if (top + mh > window.innerHeight - 8 && r.top - mh - 6 > 8) top = r.top - mh - 6;
      el.style.left = left + "px";
      el.style.top = top + "px";
      requestAnimationFrame(() => el.classList.add("show"));
      const outside = (e) => {
        if (!el.contains(e.target) && !anchor.contains(e.target)) {
          teardown();
          resolve(null);
        }
      };
      unbind = layer("ui-menu", () => !!_openMenu && _openMenu.el === el, () => {
        teardown();
        resolve(null);
      });
      _openMenu = { el, resolve, outside, unbind };
      document.addEventListener("pointerdown", outside, true);
      window.addEventListener("resize", _closeMenu, { passive: true });
    });
  }

  /* ================= 按钮加载态小工具 ================= */
  function btnBusy(btn, on, busyText) {
    if (!btn) return;
    if (on) {
      btn.dataset._html = btn.innerHTML;
      btn.dataset._disabled = btn.disabled ? "1" : "";
      btn.disabled = true;
      btn.innerHTML = busyText ? `<span class="spinner"></span> ${busyText}` : '<span class="spinner"></span>';
    } else {
      if (btn.dataset._html !== undefined) btn.innerHTML = btn.dataset._html;
      btn.disabled = btn.dataset._disabled === "1";
    }
  }

  return { layer, closeTopLayer, dialog, confirm: confirm_, prompt: prompt_, menu, btnBusy };
})();
