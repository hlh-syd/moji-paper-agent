# -*- coding: utf-8 -*-
"""工作区选择改造：prompt → 文件夹选择弹窗（后端目录浏览 API + 前端选择器）"""
from pathlib import Path


def patch(path, pairs):
    p = Path(path)
    t = p.read_text(encoding="utf-8")
    for old, new in pairs:
        assert t.count(old) == 1, f"[{path}] anchor x{t.count(old)}: {old[:60]!r}"
        t = t.replace(old, new)
    p.write_text(t, encoding="utf-8")
    print(path, "OK")


# ============ 1) 后端：/api/fs/list 目录浏览 ============
patch("src/web_api.py", [
    ('''    @app.post("/api/workspace/root")
    async def workspace_set_root(data: dict = Body(...)):''',
     '''    @app.get("/api/fs/list")
    async def fs_list(path: str = Query("", description="目录路径；空=盘符列表/用户主目录")):
        """列出目录下的子目录（工作区文件夹选择器用）。
        Windows 下 path 为空时返回全部存在的盘符；仅返回可见目录，无权限的目录静默跳过"""
        import string as _string
        target = (path or "").strip()
        if not target:
            drives = [f"{d}:\\" for d in _string.ascii_uppercase
                      if Path(f"{d}:\\").exists()]
            if drives:
                return {"path": "", "parent": "", "is_root": True,
                        "dirs": [{"name": f"磁盘 {d}", "path": d} for d in drives]}
            target = str(Path.home())
        p = Path(target)
        if not p.exists() or not p.is_dir():
            raise HTTPException(status_code=404, detail=f"目录不存在: {target}")
        dirs = []
        try:
            for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
                if child.is_dir() and not child.name.startswith("."):
                    dirs.append({"name": child.name, "path": str(child)})
        except PermissionError:
            dirs = []  # 无权限目录：返回空清单而非报错
        except OSError as e:
            raise HTTPException(status_code=400, detail=str(e))
        parent = str(p.parent) if p.parent != p else ""
        return {"path": str(p), "parent": parent, "is_root": False, "dirs": dirs}

    @app.post("/api/workspace/root")
    async def workspace_set_root(data: dict = Body(...)):'''),
])

# ============ 2) 前端 HTML：文件夹选择器弹窗 ============
patch("templates/index.html", [
    ('''  <!-- 划词悬浮标识（论文区选中文字时出现） -->''',
     '''  <!-- 文件夹选择器（工作区） -->
  <div class="fp-overlay" id="fpOverlay">
    <div class="fp-dialog">
      <div class="fp-head">
        <span>选择工作区文件夹</span>
        <button class="btn-mini" id="fpClose">✕</button>
      </div>
      <div class="fp-pathbar">
        <button class="btn-mini" id="fpUp" title="上级目录">↑</button>
        <input type="text" id="fpPathInput" class="fp-input" spellcheck="false"
               placeholder="输入完整路径后回车，或逐级浏览">
        <button class="btn-mini" id="fpGo">跳转</button>
      </div>
      <div class="fp-list" id="fpList">
        <div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>
      </div>
      <div class="fp-foot">
        <span class="fp-hint" id="fpHint"></span>
        <button class="btn-mini" id="fpCancel">取消</button>
        <button class="btn-mini primary" id="fpChoose">选择当前目录</button>
      </div>
    </div>
  </div>

  <!-- 划词悬浮标识（论文区选中文字时出现） -->'''),
])

# ============ 3) JS：选择器逻辑（复用原应用逻辑 applyWorkspaceRoot） ============
patch("static/js/app.js", [
    ('''async function setWorkspaceRoot() {
  const root = prompt("设置工作区根目录：论文智能体将扫描该目录下的研究项目\\n"
    + "（可以是任意盘符/任意项目的父目录，例如 D:\\\\我的科研 或 C:\\\\Users\\\\he\\\\projects）\\n"
    + "切换工作区后会话与生成物自动保存并跟随，随时可再切换。",
    state.workspaceRoot || "");
  if (!root || root === state.workspaceRoot) return;
  try {
    const resp = await api("/api/workspace/root", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root }),
    });
    state.workspaceRoot = resp.workspace_root;
    state.currentProject = null;
    state.outline = null;
    renderOutline();
    const rootEl = $("#wsRootPath");
    if (rootEl) { rootEl.textContent = `workspace: ${state.workspaceRoot}`; rootEl.title = state.workspaceRoot; }
    await loadProjects();
    scheduleSave();
    toast(`工作区已设置：${resp.workspace_root}`, "ok");
  } catch (e) { toast(`工作区设置失败: ${e.message}`, "err"); }
}''',
     '''/* ================= 工作区文件夹选择器（v20：弹窗浏览代替 prompt） ================= */
async function applyWorkspaceRoot(root) {
  try {
    const resp = await api("/api/workspace/root", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root }),
    });
    state.workspaceRoot = resp.workspace_root;
    state.currentProject = null;
    state.outline = null;
    renderOutline();
    const rootEl = $("#wsRootPath");
    if (rootEl) { rootEl.textContent = `workspace: ${state.workspaceRoot}`; rootEl.title = state.workspaceRoot; }
    await loadProjects();
    scheduleSave();
    toast(`工作区已设置：${resp.workspace_root}`, "ok");
    closeFolderPicker();
  } catch (e) { toast(`工作区设置失败: ${e.message}`, "err"); }
}

function setWorkspaceRoot() { openFolderPicker(); }

function openFolderPicker() {
  $("#fpOverlay").classList.add("open");
  state.fpPath = state.workspaceRoot || "";
  $("#fpPathInput").value = state.workspaceRoot || "";
  fpBrowse(state.workspaceRoot || "");
}
function closeFolderPicker() { $("#fpOverlay").classList.remove("open"); }

async function fpBrowse(path) {
  const list = $("#fpList");
  list.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
  try {
    const d = await api("/api/fs/list?path=" + encodeURIComponent(path || ""));
    state.fpPath = d.path || "";
    state.fpParent = d.parent || "";
    $("#fpPathInput").value = d.path || "";
    $("#fpUp").disabled = d.is_root || !d.parent;
    $("#fpChoose").disabled = d.is_root || !d.path;  // 盘符层不可选，须进入具体目录
    $("#fpHint").textContent = d.is_root ? "请进入某个磁盘后选择目录"
      : (d.path === state.workspaceRoot ? "当前已是工作区（可重新选择）" : `将选择：${d.path}`);
    list.innerHTML = (d.dirs || []).map((x) => `
      <div class="fp-row" data-p="${esc(x.path)}">
        <span class="fp-ico">📁</span><span class="fp-name">${esc(x.name)}</span>
        <span class="fp-go">›</span>
      </div>`).join("") || '<div class="empty-hint">无子目录</div>';
    list.querySelectorAll(".fp-row").forEach((r) =>
      r.addEventListener("click", () => fpBrowse(r.dataset.p)));
  } catch (e) {
    list.innerHTML = `<div class="empty-hint">${esc(e.message)}</div>`;
  }
}

function bindFolderPicker() {
  $("#fpClose").addEventListener("click", closeFolderPicker);
  $("#fpCancel").addEventListener("click", closeFolderPicker);
  $("#fpOverlay").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeFolderPicker();
  });
  $("#fpUp").addEventListener("click", () => state.fpParent && fpBrowse(state.fpParent));
  $("#fpGo").addEventListener("click", () => fpBrowse($("#fpPathInput").value.trim()));
  $("#fpPathInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") fpBrowse(e.target.value.trim());
  });
  $("#fpChoose").addEventListener("click", () => {
    const root = ($("#fpPathInput").value || "").trim() || state.fpPath;
    if (!root) { $("#fpHint").textContent = "请先进入某个目录"; return; }
    applyWorkspaceRoot(root);
  });
}'''),
])

# 绑定挂载：bindShellExtras 里加 bindFolderPicker + Esc 关闭选择器
patch("static/js/app.js", [
    ('''function bindShellExtras() {
  $("#sbNewChat")?.addEventListener("click", addSession);''',
     '''function bindShellExtras() {
  bindFolderPicker();
  $("#sbNewChat")?.addEventListener("click", addSession);'''),
    ('''    if (e.key === "Escape") closeDrawers();
  });
}''',
     '''    if (e.key === "Escape") { closeDrawers(); closeFolderPicker(); }
  });
}'''),
])

print("FOLDER PICKER DONE")
