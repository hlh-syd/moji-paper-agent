# DSH 前端样式修改方案 —— 用 deepseek-harness-master 前端重塑「墨记·论文写作智能体」

> 任务：阅读 DSH（deepseek-harness-master）前端样式 → 与墨记现状比对 → 在 **DSH 前端的基础上改**，产出本修改方案。
> 约束：**以 DSH 的前端样式为主体**，墨记的功能需求去适应它，而不是反过来。
> 日期：2026-10-24
>
> **▶ 实施状态（2026-10-24 当日）**：方案已落地为 **v4 前端**——旧 vanilla 前端（templates/index.html + static/）已退役归档（`templates/index.html.legacy-v3`、`static-legacy-v3/`），新前端为 **DSH monorepo 内新建的 `apps/moji-web`**（React 18 + Vite，直接消费 DSH `ui-theme` 设计令牌 + `ui-primitives` 组件库 + AppFrame 三栏几何），FastAPI（src/web_api.py）改为服务其 vite 构建产物。已验证：三栏布局/会话恢复/论文 Markdown（46 表格+KaTeX）/暗亮主题切换/设置页/SSE 流式对话与错误路径。构建：`cd deepseek-harness-master && pnpm --filter @deepseek-ai/moji-web exec vite build`。待办（后续迭代）：AI 检测中心面板、参考论文/项目理解等长尾功能迁移、导出菜单、文件树注入。详见文末「实施记录」。

---

## 0. 结论（TL;DR）

DSH 的 Web 前端是一套** React 18 + Vite + CSS Modules 的微内核单体仓库**（`apps/web` 壳 + `packages/client` 下约 80 个 `ui-*` 插件包），有完整的「设计令牌 → 别名 → 组件」三级样式体系、亮/暗双主题、三栏可拖拽 AppFrame 骨架和约 45 个成品基础组件。它的视觉语言是**墨色极简 + 单一品牌蓝强调**（近黑主按钮、0.5px 发丝线、大圆角面板、毛玻璃浮层）。

本方案：**保留 DSH 的令牌体系、AppFrame 三栏骨架、组件库与主题机制不动结构**，只做四类修改：

1. **品牌替换**——标题/Logo/Wordmark/favicon 从 DSH 换成「墨记」（一处 env + 三个组件）；
2. **令牌重映射（可选）**——品牌强调色通过 `--dsw-static-deepseek-*` 色阶一次性重映射，不碰任何组件 CSS；
3. **新建 4 个墨记面板模块**——论文工作区、大纲、AI 检测中心、知识库/项目，按 DSH 既有模块注册方式挂进 main/rightbar 插槽；
4. **数据层适配**——用适配器模块把墨记 FastAPI（约 75 个路由）接进 DSH 客户端宿主，替换其 agent 会话数据源。

墨记现有「左对话 46% + 右论文 54%」双栏工作流，与 AppFrame 的 `center(对话) + rightbar(默认 45%，可拖至 70%、可全屏)` 天然同构——把 `RIGHTBAR_DEFAULT_RATIO` 从 0.45 改 0.54 即一行恢复墨记比例。

---

## 一、DSH 前端样式体系盘点（阅读结果）

### 1.1 技术栈与架构

| 层 | 事实 | 出处 |
|---|---|---|
| 构建 | Vite 6 + TypeScript，pnpm workspace | `apps/web/package.json` |
| UI 框架 | React 18 + **CSS Modules**（每组件一个 `.module.css`） | `packages/client/ui-primitives/src/*` |
| 组装方式 | 微内核插槽（slots）：壳只渲 `root` 插槽，`AppFrame` 再分 `sidebar / main / rightbar / shell.bottom / shell.overlay / shell.leading` | `packages/client/ui-layout/src/client/AppFrame.tsx` |
| 面板切换 | 主区是**面板注册表**：`MainPanelId` 注册，`selectPanel(null)` 回 Conversation（对话），其他值切全局面板 | `packages/client/ui-layout/src/client/service.ts` |
| 入口 | `apps/web/index.html` → `src/main.ts` → `new AppWebEntry(#root)`；桌面/web 双通道 | `apps/web/src/main.ts` |
| 状态 | 框架 shares（PropsRuntime/PropsStore/PropsRenderSlots），零全局变量 | 各 `ui-*` 包 |

### 1.2 设计令牌（本方案的「宪法」，改它=全局换肤）

**基础层** `packages/client/ui-theme/src/styles/base.css`：

```css
--dsh-font-family-text-default: -apple-system, BlinkMacSystemFont, 'Segoe UI',
  'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', ...;   /* 中文友好 */
--dsw-font-family-brand: 'Montserrat', ...;                    /* 品牌字体 */
--dsh-font-family-code-default: 'SF Mono', 'JetBrains Mono', Consolas, ...;
--dsw-radius-xs/sm/md/lg/xl/panel: 4 / 8 / 12 / 16 / 20 / 28px; /* 六级圆角 */
--ds-transition-duration: 0.1s(fast) / 0.2s / 0.3s(slow), ease-in-out cubic-bezier(0.4,0,0.2,1);
```

**色板层** `packages/client/ui-theme/src/styles/design-platform.css`（亮/暗两份完全同构）：

- 静止色阶（`--dsw-static-*`）：`neutral`（纯灰 0–1000）、`neutral-bluish`（**带蓝灰 0–950，全 UI 骨架色**）、`deepseek`（品牌蓝 50–900，基调 `deepseek-500: rgb(65,118,230)`）、`blue/green/red/amber` 语义阶。
- 语义别名（`--dsw-alias-*`，组件只允许引用别名）：背景 `bg-base/bg-layer-1/2/3`、边框 `border-l1~l4`（亮=黑 alpha 0.04→0.16，暗=白 alpha 0.06→0.2）、文字 `label-primary/secondary/tertiary/caption`、交互 `interactive-bg-hover/active`、按钮族（**primary=近黑填充白字**，info=品牌蓝）、markdown 族、diff 族、toast/tooltip/sidebar 族……
- 暗色开关：`body[data-ds-dark-theme]`，暗色底 `neutral-bluish-950: rgb(21,21,23)`，层 875/850/800。

视觉性格总结：**白纸黑墨的墨色极简**——大面积留白、近黑主按钮、唯一强调色是品牌蓝（链接/焦点/info 按钮/选中态）、0.5px 发丝描边、28px 大圆角面板、毛玻璃菜单面（`rgba(248,249,250,0.58)` + blur）。

### 1.3 布局骨架（AppFrame）

`packages/client/ui-layout/src/client/AppFrame.tsx` + `columns.ts`，CSS Grid 三栏：

| 常量 | 值 | 说明 |
|---|---|---|
| `SIDEBAR_MIN/MAX/DEFAULT` | 264 / 420 / **280px** | 左栏（会话列表） |
| `SIDEBAR_COLLAPSED` | 56px rail（36×36 控件盒） | 折叠成图标轨 |
| `SIDEBAR_AUTO_COLLAPSE` | 1024px 视口自动折叠 | 响应式 |
| `CENTER_MIN` | 400px | 中栏（对话/主面板）保护宽 |
| `RIGHTBAR_MIN / DEFAULT / MAX` | 300px / **45%** / 70% 视口 | 右栏，支持 track / fullscreen / 拖宽 |
| 拖拽 | pointer capture + rAF 节流，开合有过渡动画 | `DragHandle` |

侧栏（`ui-sidebar/SidebarRoot.module.css`）：6px/12px 内边距、`neutral-bluish-50` 填充、激活项 `bluish-100 + deepseek-100` 强调、折叠为淡入滑出而非重排。

### 1.4 组件库（`packages/client/ui-primitives`，约 45 个）

按钮（md 36px / sm 28px；primary/ghost/outline/toolbar，outline 用 inset 0.5px 发丝线）、Input（32px，焦点=品牌蓝描边）、Modal、Menu/MenuSurface（毛玻璃）、Toast（深底白字）、Tooltip、Switch、Checkbox、SegmentedControl/SegmentedTabs、Pill、Tag、CodeCard、TerminalBlock、DiffBlock、JsonTree、ReadBlock/SearchBlock/WebBlock（工具结果渲染）、**UserMessageBubble**（`deepseek-50` 底、`radius-xl` 20px、10×16 padding）、**MarkdownText**（含 KaTeX 数学 + shiki 代码高亮 + 引用块）、SettingsForm/ConfigField（设置表单）、HoverCard、TextShimmer（加载微光）等。

设置页是**文档式全页面**（`ui-settings/SettingsRoot` + DetailDocument 模式），分区为独立 `ui-settings-*` 包；另有内容字号体系（`--dsh-content-font-size` + delta，用户可调）与 anti-FOUC 主题启动脚本（`ui-theme/src/boot-theme.ts`）。

### 1.5 品牌资产与合规

- 品牌名走 `process.env.DSH_CLIENT_TITLE`（AppFrame 读取），Logo 是 `FishLogo`（鲸鱼）+ `BrandWordmark`（Montserrat）。
- `BRAND_GUIDELINES.zh.md`：衍生项目**可用**"基于 DeepSeek Harness 构建"表述与 **DSH** 缩写；**避免**完整 "DeepSeek Harness" 商标用作产品名。→ 墨记产品名独立，仅在关于页注明"基于 DSH 构建"。

---

## 二、墨记前端现状与比对

### 2.1 现状（templates/ + static/，无构建步骤）

| 项 | 现状 | 规模 |
|---|---|---|
| 入口 | `templates/index.html`（675 行，全部静态结构） | FastAPI 直出 |
| 逻辑 | `static/js/app.js`（**5829 行单文件**，字符串拼 HTML）+ `ui.js`（251 行小组件） | vanilla JS |
| 样式 | `styles.css` 3388 行（v11「ZCode 墨岩」主题，**仅暗色**，翡翠绿 #35d49a 强调 + 径向光晕 + 磨砂）、`workspace.css`（v24 工作区）、`layout-v18`、`settings-v19` | 全局类名，无模块化 |
| 外部依赖 | 仅 highlight.js CDN | — |
| 布局 | 侧栏（264px 会话/项目/知识库三 pane）+ 顶栏 + `dual` 主体（**对话 46% ｜ 论文工作区 54%**）+ 各功能 modal | — |

### 2.2 功能视图清单（必须全部落位到新前端）

1. **对话**：SSE 流式（`/api/chat/stream`）+ WebSocket（`/api/ws`），附件栏、会话列表/归档/搜索。
2. **论文工作区 v24**：[论文|大纲] 标签页、一键生成全文进度条（节进度/字数/日志）、工具/导出菜单、配置抽屉（字数/模板/参考论文/生成大纲）、准备向导、逐节强度菜单、图表管理、预览窗。
3. **AI 检测中心 v22**（现为 modal）：整篇检测 / 上传文档检测 / 导入检测报告三模式；朱雀 API + 本地 BERT 双引擎；humanize-9B 改写复检；改写文档下载；核验后回写论文。
4. **研究项目与文件树**：工作区根切换（wsHistory 快照 + rev 乐观锁）、项目注册、文件树浏览与注入。
5. **知识库**：方法库/写作库/RAG-Anything 三库状态与更新。
6. **设置 v20**：模型供应商（智谱/DeepSeek model_registry）、温度、Ollama 本地、朱雀 Key、用户/管理员。
7. 登录认证 chip、系统健康状态。

后端接口共约 **75 个路由**（`src/web_api.py` 为主体），SSE + WS 齐备。

### 2.3 比对结论

| 维度 | DSH | 墨记现状 | 结论 |
|---|---|---|---|
| 视觉 | 墨色极简、单强调、亮/暗双主题 | 暗色+翡翠绿+光晕 | **采用 DSH**（约束）；墨记绿仅作可选重映射 |
| 圆角/边线 | 4–28px 六级、0.5px 发丝线 | 7/10/14 三级、1px | 采用 DSH 比例 |
| 字体 | 系统中文栈 + Montserrat 品牌 | MiSans + Noto Serif SC（纸面） | 采用 DSH 栈；**纸面衬线作为局部补充令牌** |
| 骨架 | 三栏可拖拽 + 面板注册表 + 停靠 | 双栏 + modal 堆叠 | **采用 DSH**：modal 型功能升级为面板 |
| 组件 | 45 个模块化成品 | 字符串拼 HTML | 采用 DSH |
| 数据 | DSH agent 宿主传输 | FastAPI 75 路由 | **适配器桥接**（唯一需要新建的数据层） |
| 数学/代码 | KaTeX + shiki | 无公式渲染 | DSH 直接补强墨记 |

---

## 三、总体思路：样式基底 = DSH，功能重建 = 墨记

在 DSH 仓库内改造（"在它的基础上改"），动的是**内容物**，不动**样式基因**：

- **原样保留**：`ui-theme` 令牌结构、`AppFrame` 骨架与拖拽、`ui-primitives` 全组件、亮/暗主题机制、`ui-sidebar` 会话侧栏、设置页文档式框架、Markdown/KaTeX/shiki 渲染、动效与 focus/a11y 体系。
- **替换**：品牌资产（标题/Logo/Wordmark/favicon）、会话数据源（DSH agent → 墨记 API 适配器）。
- **新建（按 DSH 模块范式）**：4 个墨记面板模块 + 1 个后端连接模块（见 4.5）。
- **禁止**：绕过 `--dsw-alias-*` 直接写色值、引入第二套设计令牌、改动静止色阶的命名结构。

---

## 四、具体修改方案

### 4.1 品牌层（P0）

| 改动点 | 文件 | 做法 |
|---|---|---|
| 产品标题 | 构建时注入 | `DSH_CLIENT_TITLE="墨记 · 论文写作智能体"`（AppFrame 已读该 env）；`apps/web/index.html` 的 `<title>`、`apple-mobile-web-app-title` 同步 |
| Logo | `ui-primitives/src/FishLogo.tsx`、`SiteGlyph.tsx` | 新增 `MojiLogo`（墨记印记），侧栏 logoRow 与关于页替换；FishLogo 保留在"基于 DSH 构建"署名处 |
| Wordmark | `ui-primitives/src/BrandWordmark.tsx` | 文字改「墨记」，Montserrat 仅用于拉丁字符，中文自动落系统黑体栈（`--dsw-font-family` 回退已保证） |
| favicon/manifest | `apps/web` 的 favicon.svg 等 | 换墨记图形；manifest name 改墨记 |
| 合规 | 关于/设置页 | 注明「基于 DeepSeek Harness (DSH) 构建」，满足 BRAND_GUIDELINES |

### 4.2 令牌层（P0 保留原色；墨记绿为可选开关）

**主案（默认）**：完全沿用 DSH 色板——"以它的样式为主"的最忠实执行，品牌辨识由 wordmark/logo 承担。

**可选案（品牌色重映射）**：若需墨记翡翠绿识别度，**只改一处**——`design-platform.css` 中 `--dsw-static-deepseek-50/100/…/600` 共 10 个变量（亮暗两份，共 20 行）映射为墨记绿阶（如 500 ← `#21b584`），所有 alias（链接/焦点/info 按钮/气泡/选中态）自动跟随，组件 CSS 零改动。这是该体系预留的唯一换肤接缝，用法与 DSH 官方一致。

**新增局部令牌（不进平台层）**：论文纸面衬线字体，仅在论文面板模块内声明：

```css
/* ui-paper-workspace 内局部 */
--moji-font-paper-serif: 'Noto Serif SC', 'Source Han Serif SC', 'SimSun', serif;
```

### 4.3 布局落位（AppFrame 三栏 ↔ 墨记功能映射）

| AppFrame 区位 | 墨记功能 | 说明 |
|---|---|---|
| **sidebar**（280px，折叠 56px rail） | 会话列表/搜索/归档（现有 `ui-sidebar`+`ui-session` 直接复用）；底部：项目·文件树、知识库状态（两个折叠分组，复用 sidebar pane 模式）；auth chip + 设置入口 | 墨记三 pane 收纳为侧栏分组，不再占独立抽屉 |
| **center（main 插槽）** | `null`=对话 Conversation（SSE 流式，复用 ui-chat/ui-conversation 的 Markdown/KaTeX 渲染）；注册面板：`outline`（大纲）、`aicenter`（AI 检测中心）、`kb`（知识库管理） | modal 型功能升级为 DSH 全局面板，经 `ctx.layout.selectPanel()` 切换 |
| **rightbar（默认 45%→改 54%，可拖至 70%，可全屏）** | **论文工作区常驻面板**：[论文\|预览] 内容、全文生成进度条（StateDot + 进度条 + TextShimmer）、工具/导出菜单（Menu）、配置抽屉（右栏内浮层，复用 HoverCard/Modal） | 墨记核心「左对话右论文」工作流原样保留；`columns.ts` 一行改 `RIGHTBAR_DEFAULT_RATIO = 0.54` |
| shell.bottom / overlay | 状态条（健康状态/全文任务角标）、Toast/全局浮层 | 复用 |

**响应式沿用 DSH**：<1024px 侧栏自动折叠 rail；窄屏时右栏先收 track 再消失、中栏保 400px——比墨记现有 1360/1100/960 三档断点更成熟，直接采用。

### 4.4 组件映射表（墨记控件 → DSH primitives）

| 墨记现有 | DSH 替代 | 备注 |
|---|---|---|
| `.btn / .btn.primary` | `Button`（md/primary） | 近黑填充白字 |
| `.btn-mini` | `Button sm`（ghost/outline） | 28px 紧凑 |
| `.ws-tabs`（论文\|大纲） | `SegmentedTabs` / `SegmentedControl` | |
| 设置 modal `.set-row` 表单 | `SettingsForm` + `ConfigField` | 直接获得校验/布局 |
| 各类 `uw-` 大 modal（AI 检测中心等） | 升级为 main 面板（`DetailDocument` 文档式布局）或 `Modal` | 见 4.3 |
| 右键菜单/工具菜单 | `Menu` + `MenuSurface`（毛玻璃） | |
| 提示条/浮 toast | `Toast`（深底白字） | |
| 会话气泡 | `UserMessageBubble`（20px 圆角气泡）+ `MarkdownText` | **新增 KaTeX 公式渲染**（墨记原本没有） |
| 代码块（hljs CDN） | `CodeBlock`（shiki，本地打包，去 CDN） | |
| 进度条/生成中状态 | `StateDot` + `TextShimmer` + 自建进度条（用 `--dsw-alias-state-*`） | |
| 文件树 | `AnimatedRows` + `FileTypeIcon` | |
| 开关/勾选 | `Switch` / `Checkbox` | |

### 4.5 新建模块（按 DSH 包范式，全部为增量）

| 新包（`packages/client/`） | 插槽 | 内容 |
|---|---|---|
| `ui-paper-workspace` | rightbar | 论文编辑/预览、全文生成进度、工具与导出菜单、配置抽屉；纸面衬线排版；调 `/api/outline/* /api/sections/* /api/fulltext/* /api/export/* /api/figures/*` |
| `ui-outline` | main 面板 | 大纲树、模板/估算、逐节操作；调 `/api/outline/*` |
| `ui-aicenter` | main 面板 | 三检测模式、报告/段落明细、9B 改写复检、下载/回写；调 `/api/aicenter/* /api/doccheck/* /api/sections/ai_check` |
| `ui-moji-projects-kb` | sidebar 分组 | 工作区根切换（保留 rev 乐观锁逻辑）、项目注册、文件树、三知识库状态；调 `/api/workspace* /api/projects* /api/fs/* /api/kb/* /api/rag/*` |
| `moji-backend`（连接模块） | 数据层 | 墨记 FastAPI 传输适配器：SSE `/api/chat/stream` ↔ conversation 事件流、`/api/ws`、REST 封装、`/api/auth/*` 登录态；替换 DSH agent 宿主连接 |

每个包照抄现有 `ui-*` 的结构（`src/client/index.ts` + `contract/slots.ts` + CSS Modules + package.json workspace 引用），在 `packages/client/modules` 注册面板 id。**CSS 纪律：只引用 `--dsw-alias-*` 与 `--dsw-radius-*`/motion 令牌，禁止裸色值**（沿用 DSH 检查惯例）。

### 4.6 数据层适配要点

- 会话模型：墨记会话（消息数组 + 附件）映射到 DSH conversation 的 user-text/markdown 渲染管线；DSH 的工具调用/审批机械与本产品无关，不启用对应模块即可（微内核按需装配）。
- 流式：SSE 逐 token → MarkdownText 的增量渲染（DSH 自带 incremental.ts）。
- 部署：`vite build` 产 `apps/web/dist`，由墨记 FastAPI `StaticFiles` 挂载同源提供（替代现 `templates/index.html`），`/api/*` 同源无 CORS；保留 `启动论文智能体.bat` 语义。
- 回滚：旧前端保留在 `legacy-ui/` 分支或目录一个版本周期。

### 4.7 字体与中文排版

正文/界面：DSH 系统中文栈（PingFang/雅黑），与墨记 MiSans 意图一致，无需引入字体文件；品牌字 Montserrat 仅 Wordmark 拉丁字符；代码字体栈已含中文回退（注释明确处理了 Windows CJK 掉 SimSun 的问题）。论文纸面：局部 `--moji-font-paper-serif`（4.2）。内容字号沿用 `--dsh-content-font-size` 用户可调体系。

### 4.8 主题

DSH 亮/暗双主题全量保留（`AppearanceRow` 设置项照抄）。墨记原 dark-only 用户的习惯由「默认跟随系统」覆盖；论文纸面在两种主题下分别以 `bg-document-preview` 层呈现（DSH 已有该令牌语义）。

---

## 五、分阶段实施计划

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P0 壳与品牌**（先跑通） | 4.1 品牌替换 + `moji-backend` 登录/健康/会话列表 + sidebar 会话 + center 对话 SSE 流式（KaTeX 生效） | 能登录、能列出/新建会话、能流式对话，界面无 DSH 字样 |
| **P1 论文工作区** | `ui-paper-workspace` 挂 rightbar（比例 0.54）+ `ui-moji-projects-kb` 侧栏分组；工作区根切换（rev 锁）迁移 | 左对话右论文并排、可拖宽/全屏；项目/文件树/知识库可用 |
| **P2 大纲与生成** | `ui-outline` 面板 + 全文生成进度条 + 工具/导出菜单 + 配置抽屉 | 一键生成全文全流程可视、可取消、可导出 |
| **P3 AI 检测中心** | `ui-aicenter`：三模式、双引擎、9B 改写复检、下载与回写 | 原 AIC modal 全功能在面板内闭环 |
| **P4 设置页** | 复用 `SettingsRoot` 文档式框架，迁入模型供应商/朱雀/本地模型/账号分区 | 全部 `data-set` 字段与新 API 对齐 |
| **P5 打磨** | 快捷键（ui-shortcuts）、focus 环、动效审查、断点走查、去 hljs CDN | DSH 视觉走查清单全过 |

每阶段独立可发布；P0 结束即可作为日常使用底座（对话+会话）。

---

## 六、风险与规避

| 风险 | 说明 | 规避 |
|---|---|---|
| 微内核学习成本 | cordis 插件/shares 范式与 vanilla JS 差异大 | 严格"抄结构"：每个新包对照一个最像的现有包（如 `ui-aicenter` 对照 `ui-settings-plugins`） |
| 会话模型错配 | DSH conversation 面向 agent 轮次/工具审批 | 只装配 user-text/markdown 管线，不注册工具渲染模块；`moji-backend` 做字段映射层，墨记 API 不改 |
| 单文件 5829 行迁移遗漏 | app.js 内隐式耦合多 | 以 2.2 功能清单为验收清单逐项打勾；旧 UI 保留一个周期供对照 |
| 品牌合规 | 商标规范 | 产品名只用「墨记」，署名「基于 DSH 构建」 |
| 主题回归 | 重映射色阶后暗色对比度 | 只改 static-deepseek 色阶、不动 alias；P5 走查亮/暗两遍 |
| Windows 中文回退 | 代码字体掉宋体 | DSH 栈已处理，勿删 `PingFang SC/Microsoft YaHei` 尾巴 |

---

## 附录 A：墨记视图 → DSH 落位总表

| 墨记现有 | 新落位 | 用到的 DSH 资产 |
|---|---|---|
| 侧栏·会话列表/搜索/归档 | sidebar | ui-sidebar、ui-session |
| 侧栏·项目与文件树 pane | sidebar 分组 | ui-moji-projects-kb（新）、AnimatedRows、FileTypeIcon |
| 侧栏·知识库 pane | sidebar 分组 | ui-moji-projects-kb（新）、StateDot |
| 顶栏 crumbs/状态 | shell.bottom 状态条 + DocumentTitle | ConnectionIndicator |
| 对话区（SSE/WS/附件） | center·Conversation | ui-chat、ui-conversation、UserMessageBubble、MarkdownText+KaTeX |
| 论文工作区（论文页/进度/工具/导出/配置） | rightbar 常驻 | ui-paper-workspace（新）、Menu、SegmentedTabs、Modal |
| 大纲页 | main 面板 outline | ui-outline（新） |
| AI 检测中心 modal | main 面板 aicenter | ui-aicenter（新）、DetailDocument、Pill/Tag |
| 设置 modal | 设置页（文档式） | SettingsRoot、SettingsForm、ConfigField |
| 准备向导 | 论文面板内分步浮层 | Modal + 步进 |
| 登录 | boot 页 + auth chip | ui-theme boot-page、login 输入令牌 |

## 附录 B：涉及文件清单（改/建）

**改（品牌与一行比例）**
- `apps/web/index.html`（title/manifest/favicon）
- `apps/web` 构建 env：`DSH_CLIENT_TITLE`
- `packages/client/ui-layout/src/client/columns.ts`（`RIGHTBAR_DEFAULT_RATIO 0.45→0.54`）
- `packages/client/ui-primitives/src/FishLogo.tsx` / `BrandWordmark.tsx`（旁路新增墨记资产，勿删原组件——署名仍用）
- 可选：`packages/client/ui-theme/src/styles/design-platform.css`（deepseek 色阶 → 墨记绿阶，20 行）

**建（新包，全部增量）**
- `packages/client/ui-paper-workspace/`
- `packages/client/ui-outline/`
- `packages/client/ui-aicenter/`
- `packages/client/ui-moji-projects-kb/`
- `packages/client/moji-backend/`（FastAPI 传输适配）
- 各包在 `packages/client/modules` 的注册项

**退役（一个版本周期后）**
- `templates/index.html`、`static/js/app.js`、`static/js/ui.js`、`static/css/*`（FastAPI 改挂 `apps/web/dist`）

---

## 附录 C：实施记录（v4 · 2026-10-24）

### C.1 最终架构（取代第五节的 P0–P5 分阶段计划：P0/P1 已一次性落地）

```
墨记 FastAPI (src/web_api.py)
  └─ GET /            → deepseek-harness-master/apps/moji-web/dist/index.html
  └─ /assets, /favicon.svg → dist 静态产物（hash 文件名，免缓存击穿）
  └─ /api/*           → 不变（75 路由零改动；主题仍随 workspace state 持久化）

apps/moji-web（新，React 18 + Vite 6 + TS）
  ├─ 消费 @deepseek-ai/dsh-client-ui-theme      （DSH 设计令牌：base/design-platform/scrollbar/focus/shiki css）
  ├─ 消费 @deepseek-ai/dsh-client-ui-primitives （Button/Input/Switch/SegmentedTabs/Modal/Toast/
  │                                               MarkdownText(KaTeX+GFM)/UserMessageBubble/StateDot/Pill…）
  ├─ src/layout/columns.ts  ← DSH AppFrame 几何原样移植（rightbar 默认 0.54 = 墨记比例）
  ├─ src/layout/AppShell.tsx ← 三栏 + rAF 节流拖拽把手（DSH DragHandle 移植）
  ├─ src/views/{Sidebar,ChatView,PaperWorkspace,AuthOverlay,SettingsPage}.tsx
  ├─ src/{api,store}.ts     ← FastAPI 数据层 + 手写 store（rev 乐观锁保存语义保留）
  └─ src/app.css            ← 只写布局；色彩/圆角/阴影一律走 --dsw-* 令牌
```

### C.2 关键实现决策

1. **不接入 DSH Node 宿主**：DSH 完整客户端需 `__DSH_BOOT__` 启动图（cordis 插件树 + RPC），与 FastAPI 不兼容。改为消费其**零 cordis 的纯组件层**（ui-primitives 自述 "Pure React atoms, zero cordis"）——这正是"以 DSH 前端为基础"的可交付形态。
2. **DSH 依赖声明修复**：ui-primitives/ui-theme 的运行时依赖（clsx/katex/shiki/micromark 系/zustand 等）原在 devDependencies（宿主自产自销），外部消费时 pnpm 严格隔离下解析不到——已移入 dependencies（21+1 项），属于对 DSH 仓库的唯一必要改动。
3. **保存语义保留**：PUT /api/workspace 全量替换 + rev 乐观锁 + 409 整页刷新（后端零改动）；登录 token 沿用 `paper_agent_token` 键，老用户登录态无缝。
4. **主题**：body[data-ds-dark-theme]（DSH 暗色板）↔ 无属性（DSH 亮色板）；跟随账号保存的 theme 值，设置页开关即时切换；index.html 内联反闪屏脚本。

### C.3 验证结论（Chrome 实测）

- 三栏：280px 侧栏 / center ≥400px / 右栏 54%（grid 模板解列与 DSH 同构），拖拽把手悬停浮现品牌蓝。
- 会话恢复：旧 workspace state 的会话/项目/大纲全部正常加载（含 8484 字论文章节）。
- 论文渲染：DSH MarkdownText 全量接管——46 张 GFM 表格、57 个小节标题、KaTeX 公式（$..$ 中文内容仅产生 KaTeX 原生 warn，不影响渲染）、图片路径占位。
- 主题切换：暗色 #151517/#1b1b1c、亮色 #fff/#f9fafb，全部为 DSH 色板原值。
- 流式对话：用户气泡（UserMessageBubble）→ SSE status「思考中…」→ chunk 增量 → done/error 落库。当前环境缺 DEEPSEEK_API_KEY，错误路径亦验证通过。
- 修复的 bug：① AppShell 漏 import useEffect 致白屏；② App.tsx 编辑事故吞换行；③ 论文正文初版轻量 md 不渲染表格/标题 → 改 MarkdownText；④ favicon/静态路由 404。

### C.4 遗留（后续迭代清单）

- AI 检测中心（/api/doccheck/* + 朱雀 + 9B 改写）→ 计划挂 main 面板
- 配置抽屉（模板/参考论文/项目理解/生成大纲四步）与导出菜单
- 项目切换器与文件树注入（/api/projects/* + /api/file/*）
- 全文任务 WS 实时化（现为 1.5s 轮询）；会话归档/置顶

### C.5 测试记录（2026-10-25 全量回归）

**通过项**
- 基础：`/api/health` 200；index/assets/favicon 全 200（favicon 307 已修）；控制台零 JS 错误
- 会话：新建/搜索过滤/切换/删除/重命名（Enter 与 blur）；重命名持久化（服务端落盘验证）
- 对话：发送→SSE status「思考中…」→chunk/done/error 全路径；错误消息落为助手行；状态条与按钮状态恢复
- 论文区：大纲 7 张章节卡（字数/要点）；论文 46 张 GFM 表格 + 57 小节标题 + 公式渲染；一键全文在缺 Key 环境优雅失败（Toast 提示真实原因）
- 设置页：模型下拉（glm-5.3/flash/deepseek-4.1-flash）读取；温度修改→保存（服务端 POST 200 热生效）→Toast；主题开关
- 布局：侧栏折叠 56px 图标轨（4 按钮）/展开恢复；侧栏拖拽钳位 264–420；右栏拖拽（补齐）+ 双击复位（补齐）；<1024px 自动折叠、无横向滚动、center 保 400px
- 持久化：主题（修复后）跨刷新/冷重启跟随 workspace state；会话/消息/论文全量恢复
- 认证：单用户模式正确显示；登出入口（新增）；401→清 token→重载路径

**本轮修复（4 个）**
1. favicon 目录挂载 307 → 改 FileResponse 路由（web_api.py）
2. 会话重命名命令式 DOM 与 React 脱同步 → 改受控组件（Sidebar.tsx）
3. 主题切换不落盘导致刷新回滚 → applyTheme 内 scheduleSave（store.ts），workspace state 为唯一真源
4. boot 读不存在的 `auth_required` 字段（RBAC 开启时登录页永不弹）→ 按 `enabled && users>0` + `/api/auth/me` 401 判定；legacy 伪用户不再显示为「管理员/退出」（store.ts/AppShell/Sidebar）

**补齐（2 个）**：右栏（论文区）拖拽把手；登出入口。**增强**：把手双击复位。

**已知限制 / 未测**
- RBAC 注册→登录→接管数据链路未真机测试（首个注册用户会接管现有数据，属破坏性变更；逻辑与旧版一致）
- 多条 Toast 并发时同点重叠（DSH Toast 单条设计，portal 到 body 固定定位）
- 对话流式 chunk 增量渲染依赖真实模型 Key（当前环境回退路径已验）；RAG embedding 服务宕机使首响应 ~19s（环境问题）

### C.6 前端已按需求全部移除（2026-10-25）

应用户指令「全部删除」，当日完成：
- 删除 `deepseek-harness-master/apps/moji-web/`（v4 DSH 基座前端，含构建产物）
- 删除旧前端归档 `static-legacy-v3/`、`templates/`
- `src/web_api.py` 回滚：移除 /assets 与 favicon 挂载；根路由 `/` 改为「API 服务提示页」（指向 /api/docs 与 /api/health）；移除 StaticFiles 导入
- 验证：`/` 200 提示页、`/api/health` `/api/docs` `/api/workspace` 200、`/assets/*` 404、启动无错误
- 现状：**纯 API 模式**（75 个接口 + Swagger /api/docs）。旧 vanilla 前端仍可从 git 历史恢复（static/、templates/ 为已跟踪删除）；v4 前端未提交过 git，不可恢复
- DSH monorepo 本体保留（仅其 ui-primitives/ui-theme 两个包的依赖声明修正仍在，属无害的准确化改动）

### C.7 DSH 前端源已迁至根目录 dsh-frontend/（2026-10-25）

应用户指令，`deepseek-harness-master` 的前端部分（apps/web + packages/client 全部 + 构建基建/lockfile/patches）已**移出**至仓库根 `dsh-frontend/`（137MB，58 个 ui-* 包完整、lib/ 编译产物保留、apps/web/dist 参考构建保留、包内 node_modules 已删可重建）。使用说明见 `dsh-frontend/README-使用说明.md`（路径 A=只取设计体系直消费，路径 B=回装 monorepo 跑完整 DSH Web）。原 monorepo 仅剩宿主/CLI/桌面等非前端部分。

### C.8 v5 前端重建并全量对接后端（2026-10-26）

应用户指令「对接我们的后端接口；没有的前端能力放设置里；论文/大纲全量展示在主页」：

**架构**：仓库根新建 `frontend/`（Vite+React 独立工程），通过 pnpm workspace（根 package.json + pnpm-workspace.yaml 收窄 6 包）消费 `dsh-frontend/` 的 ui-primitives/ui-theme/store/util 产物；vite alias 显式指向 lib/index.js（symlink 跨目录解析不稳）。构建 `cd frontend && npx vite build`；FastAPI `/`→frontend/dist，/assets、/favicon.svg 挂载已恢复。

**主页（对话+论文全量）**：三栏（会话/对话/论文）；论文区【全量】= 标题（双击改名 →/api/outline/meta）+ 7 章 15 小节完整正文（MarkdownText：表格/公式）+ 每章操作条（重生成/润色/去AI味/深度/体检/编辑/恢复，SSE chunk 直写正文）；一键全文+进度+取消；导出 Word/PDF/MD；全文自查报告弹窗；清空正文；大纲 Tab 全量卡片（章+小节状态/字数/要点）；生成大纲向导（模板字典/领域/字数估算 Query/参考论文/附加要求）。

**设置页 10 分区**（主页未承载的能力全在此）：常规（/api/info + /api/fs/list 工作区浏览选择）、外观（主题）、项目与理解（注册/切换/理解 Agent/深化画像/文件树）、参考论文（上传解析/骨架提取）、排版样式（查看/套入/撤销）、图表管理（清单/上传替换）、AI 检测中心（整篇检测/上传检测/导入报告核验/段落勾选 9B 改写复检/下载/回写）、知识库（三库计数/更新/RAG 状态/批量入库/检索问答）、模型设置、关于。

**测试修的 4 bug**：①模板接口返回字典非数组；②estimate 为 Query 参数；③KB 键名 method_papers/writing_methodology；④体检 markers 对象数组渲染 [object Object]。实测通过：全量渲染（7 章/15 小节/9 表格）、体检真通（启发式 42 分+双引擎降级）、KB 计数 1470/1156、6 个已注册项目、暗/亮主题、控制台零错误。

### C.9 论文/大纲切换条上移（2026-10-26 收尾）

应用户反馈「右侧仍然显示论文/大纲」→ 明确为「📄 论文 | 🗂 大纲 通过顶部切换条切换」：

- 新增主区顶栏 `.main-topbar`（44px，跨中栏+右栏，grid 行 1）：左侧项目面包屑（📁 当前项目），右侧 `SegmentedTabs`（sm）承载「📄 论文 | 🗂 大纲」切换；未选项目或右栏收为 0 时自动隐藏。
- 右栏 `.ws-head` 不再包含视图切换，仅保留论文操作按钮（生成大纲/一键全文/导出/自查/清空），行高 42px。
- 切换状态提升到 store：`paperTab: 'paper' | 'outline'`，顶栏与右栏共用（PaperWorkspace 只读该状态）。
- 网格改为 `44px 1fr auto` 三行；侧栏仍通高，右栏跨内容+状态行；拖拽把手按 `data-side` 区分起点（右栏把手从顶栏下沿 44px 开始）。
- 验证：亮/暗主题、1440/1040/780 宽度、切 Tab 正文↔大纲、设置页全屏覆盖（顶栏不露出）、控制台零错误；`cd frontend && npx vite build` 通过。

### C.10 设置页右侧复用论文/大纲（2026-10-26）

应用户补充「设置页右侧空白区域用于显示论文/大纲（切换条切换），和工作区一样」：

- 设置页改为三列：`210px 导航 | 设置内容 | 论文区`；论文区宽度沿用工作区口径（`rightbarWidth` 拖拽值，缺省 54% 视口，且给设置内容保底 520px），窄于 360px 或未选项目时自动隐藏、设置内容独占。
- 论文区顶部复用 `.mtb-project` + `PaperViewSwitch`（与工作区同一 `paperTab` 状态），下方直接挂 `PaperWorkspace`（全文/大纲、逐节操作、一键全文、导出全部可用）。
- 双实例避免：设置打开时 `AppShell` 不再挂载工作区论文面板（`rightbar-col` 为空），关闭设置后重新挂载。
- `.settings-page` 补 `grid-template-rows: minmax(0, 1fr)`（否则隐式行被论文高度撑到 3 万 px，内容与论文都不滚动）。
- 验证：2133/1333/1040 宽度（1040 时论文区自动隐藏）、设置内切换论文↔大纲、关闭设置恢复工作区论文、控制台零错误。

### C.11 「理解项目」点击无效修复（2026-10-28）

用户反馈设置页「🧠 理解项目」点了没反应。定位为请求体形状不符，后端 422：

- `POST /api/project/understand/start` 的模型是 `RegisterProjectRequest{name, path}`，前端只发了 `{name}` → 422。改为从已注册项目列表取 `path` 一并提交（与旧版 vanilla 前端一致）。
- 顺带修 `POST /api/profile/enrich`：后端参数是 **Query**（`?project_name=&limit=`），前端误发 JSON body → 422。改为 query，并在启动后回读 `/api/profile/enrich/status` 显示进度。
- 补全理解闭环：启动后 2.5s 轮询 status；`awaiting_answers` 时把后端的澄清问题渲染成单选/多选/文本表单，「提交并生成 / 跳过」调用 `/api/project/understand/answers`；`done` 时自动拉取并展示《研究项目主体》（MarkdownText，可收起）；状态以 chip 呈现（未开始/通读分析中/待确认/生成中/完成/失败），进行中禁用按钮。
- `api.ts` 错误信息解析增强：FastAPI 422 的 detail 数组转成 `字段: 原因`，不再显示 `[object Object]`。
- 验证：点击后实际请求体 `{"name":"script","path":"F:\AAAAA课题\script"}`、状态 chip 与按钮进入「进行中」、轮询回落后恢复、深化画像 URL 为 `?project_name=script`、控制台零错误。

### C.12 项目理解「缺少 pipeline 字段」修复（2026-10-28）

用户实测截图：理解流程通了但状态为「失败 · 分析结果缺少 pipeline 字段」。查 `logs/llm_calls.jsonl`：该次调用正常返回 19946 字符，超过 `max_tokens=16384` 被截断，JSON 不完整 → `_parse_json` 返回 `{}` → 报缺 pipeline。修复（`src/project_analyst.py`）：

- `_fresh_llm(min_max_tokens=0)`：理解分析调用给 `min_max_tokens=32768`，从根上消除截断。
- `analyze()` 解析失败时带 `RETRY_PROMPT`（压缩篇幅要求 + 上次输出尾部）自动重试一次；仍失败时报错附输出长度与尾部片段，便于定位。
- 实测：POST `/api/project/understand/start {name:script, path:F:/AAAAA课题/script}` → 约 25 秒到 `awaiting_answers`，6 个澄清问题（C指数归属/18vs25基因/Uno C 口径/创新点排序等）全部正常生成，前端问答表单可直接渲染。
- 注意：curl 传 Windows 反斜杠路径需转义或用正斜杠（`F:/...` 后端同样接受）。

### C.13 「重新生成大纲」验证与防呆（2026-10-28）

用户反馈「点击重新生成大纲 无法按照要求进行」。端到端实测（浏览器真实点击 + 网络面板）：

- 流程本身已通：modal（模板 3/领域 5/参考论文 3）→ POST /api/outline/generate → 约 2 分钟 200 → 落盘 → UI 自动刷新 7 章。两轮真实生成均成功，章节顺序/字数分配与 config.yaml 的 chinese_journal 模板（其他→前言→摘要→材料与方法→结果→结论→讨论）完全一致。
- 之前「点了没反应」的原因：用户页面停留在重启前的旧 bundle（理解流程修复前构建），且服务端曾处于旧进程；刷新即恢复。
- 新增防呆：已有大纲时点「生成大纲」先 confirm 提示将覆盖现有结构与正文（原先无确认，误触即丢稿）。

### C.14 深化画像排查与失效图跳过（2026-10-29）

用户反馈「深化画像点击无法工作」。排查结论：后端无错误——点击时上一轮分析仍在运行（409 拒绝并发），前端 toast 一闪而过易被当作无响应。实测三轮均正常：视觉模型逐图分析（每轮上限 24 张，max_profile_images），进度经 /api/profile/enrich/status 可见，desc 持久化回画像（47→54→66 张）。

修复：画像中登记但磁盘已不存在的图片（目录改名/删除遗留）每轮重复失败拖水分——现标记 `missing:true` 持久化，后续轮次直接跳过（已标记 12 张）。后端改动已随服务重启生效。

### C.15 深化画像提速与视觉空描述根因修复（2026-10-29）

用户反馈「仍然无法快速进行工作」。四项改造（后端随服务重启生效，前端已重建）：

1. **并发**：视觉分析由串行改 `asyncio.gather` + Semaphore(4)，单轮速度约 4 倍。
2. **一次清完**：默认一轮处理全部剩余图（原先每轮上限 24 张要点 5 轮）；`limit` 参数仍可显式截断。
3. **前端自动轮询**：点「🔬 深化画像」后每 2.5s 刷新进度 chip（进行中 x/N），完成自动 toast，不再需要手点「画像进度」。
4. **SVG/空描述根因**：
   - svg/tiff 视觉模型不收 → 自动找同目录同名 png/jpg 替身分析；无替身标记 `vision_skip` 持久跳过；
   - 部分图「空描述」根因 = vision_agent 直连 DeepSeek 未带 `extra_body(thinking disabled)`，输出进 reasoning_content、content 为空——已带上 route.extra_body，修复后 8/8 全部成功；
   - 空描述现在记录 warning 并计入 status.failed，不再静默。
- 最终：script 项目 190 图 → **151 张有描述**、39 张文件已不存在（`missing` 持久跳过）、0 待处理；再点深化画像会提示「全部图片已有描述，无需深化」。

### C.16 顶栏全文进度条 + 全文状态字段修复（2026-10-29）

用户要求「生成全文时的进度条，放在论文/大纲左边」：

- 新增 `FulltextProgress` 组件：进行中在「📄 论文|🗂 大纲」切换条**左侧**显示「🚀 全文 2/7 + 110px 细进度条」，工作区顶栏与设置页论文区顶栏两处都有；悬停显示当前章节与累计字数。
- **修复重大字段错位**（v5 重写时臆断的后端字段，导致全文进度 UI 从未显示过）：后端实际返回 `{state:'running', current:<节id>, finished:<完成数>, total_words, events:[{type,time,title,ai_score}]}`，前端却按 `{active, current:<数字>, words, log}` 读取。新增 `normalizeFulltext()` 归一化层统一映射，`ft-strip` 详细进度条（节数/字数/事件日志/取消）随之恢复工作。
- 空闲探测（10s）发现任务启动即自动接管 1.5s 高频轮询——从对话里启动的全文任务也能显示进度。
- 实测：任务真实运行中（2/7 节、14719 字），顶栏「🚀 全文 2/7」29% 填充、位于切换条左侧；ft-strip 事件日志正常滚动。

### C.17 论文正文图片不显示修复（2026-10-29）

用户反馈「图片并没有插入」：正文里只有「图1-1 图05_批次统计与相关性.png」这类文件名占位，无真实图表。

根因：正文 markdown 的图片是**项目相对路径**（如 `RNA签名-04/32_共线性/figures/fig1_corr_heatmap.png`），而 DSH `MarkdownText` 的安全过滤只放行绝对 `http(s)` URL（相对路径 → `imageSource()` 返回 undefined → 降级为 alt 文本）。后端 `/api/file/raw?path=` 需要项目根下的绝对路径，且 RBAC 下支持 `?token=`。

修复（`PaperWorkspace.tsx`）：渲染前用 `withImages(md, projectRoot)` 把相对引用改写为
`${location.origin}/api/file/raw?path=<projectRoot/相对路径>&token=<AUTH.token>`；
`projectRoot` 取 outline 的 `project_path`（`PaperDoc` → `SectionBlock` 逐层传入，小节同样处理）。
实测：论文区 28 张引用全部渲染为真实 `<img>`（含 2708×2458 热图），Hover 可放大、可拖拽，导出 docx/pdf 早已按 project_path 解析，不受影响。

### C.18 导出 Word/PDF/MD 路由错误修复（2026-10-29）

用户反馈「无法正确导出 word」。排查：前端三个导出按钮写成 `/api/outline/{项目}/export/{fmt}`，后端实际路由是 `/api/outline/export/{fmt}?project_name=`，请求一直 404（下载到的是 JSON 错误体）。修复：

- `PaperWorkspace` 三个按钮改为 `/api/outline/export/{docx|pdf|md}?project_name=<项目>`（`download()` 自动追加 token）。
- 顺带修会话导出：`/api/conversation/{id}/export` 后端并无此路由（同样 404），改为前端用 `downloadText()` 生成 Markdown Blob 直接下载（`api.ts` 新增该 helper）。
- 实测：Word → 200，6.16 MB，解包校验为合法 docx（44 条目、**25 张内嵌图片**、含 w:drawing）；PDF → 200，2.36 MB，15.6s；MD → 200，91 KB；浏览器点击 Word 成功落盘 `script.论文草稿_*.docx`。
