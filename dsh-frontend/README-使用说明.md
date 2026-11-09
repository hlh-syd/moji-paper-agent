# dsh-frontend — DeepSeek Harness (DSH) 前端源码（自 monorepo 抽出备用）

> 2026-10-25 自 `deepseek-harness-master/` 移出（move，非复制）：`apps/web` + `packages/client` 全部，
> 附构建基建文件。原 monorepo 中已不含前端部分（apps 下剩 cli/desktop/desktop-host/plugin-market-generator）。
> 用途：**为墨记前端重建做准备**——架构决策、踩坑与完整测试记录见仓库根《DSH前端样式修改方案.md》附录 A–C。

## 目录结构（三层）

```
dsh-frontend/
├─ apps/web/                  # ① 入口应用（薄壳）：index.html + src/main.ts（AppWebEntry 启动）
│   └─ dist/                  #    参考构建产物（index.html + assets/ 120 个 hash 文件）
├─ packages/client/           # ② 前端包全集
│   ├─ ui-theme/              #    ★ 设计令牌单一真源（src/styles/{base,design-platform,…}.css）
│   ├─ ui-primitives/         #    ★ 纯 React 组件库（zero cordis，lib/ 为已编译可直消费产物）
│   ├─ ui-layout/ + ui-dockkit/ #  ★ 三栏 AppFrame 几何与停靠
│   ├─ web/                   #    壳库（boot/mount/启动屏）
│   ├─ ui-*(58 个)            #    聊天/会话/侧栏/设置/工具卡…各功能面板
│   └─ connection/modules/store/locale/…  # 宿主 RPC、模块系统、状态等支撑包
├─ pnpm-workspace.yaml / package.json / pnpm-lock.yaml   # ③ 构建基建（自 monorepo 根复制）
├─ tsconfig*.json / tsdown.config.ts / vitest.shared.ts
└─ patches/                   #    依赖补丁（lexical、node-pty）
```

## 后续使用的两条路径

### 路径 A：只取设计体系（推荐，墨记 v4 已验证 ✅）
在任意 Vite+React 工程中直接消费两个包（无需整个 monorepo）：
```ts
import '@…/dsh-frontend/packages/client/ui-theme/src/styles/base.css'        // 令牌
import '@…/dsh-frontend/packages/client/ui-theme/src/styles/design-platform.css'
import { Button, MarkdownText, … } from '@…/dsh-frontend/packages/client/ui-primitives'
```
- 用 `ui-primitives/lib/`（已编译 ESM + CSS Modules）而非 src，需其 dependencies（已修正为运行时声明，
  见包内 package.json）；三栏几何照抄 `ui-layout/src/client/columns.ts`。
- 暗色开关：`body[data-ds-dark-theme]`；亮暗色板见 `ui-theme/src/styles/design-platform.css`。
- 墨记 v4（已删）即此路径建成并通过全量测试，完整复刻要点见方案文档附录 C.2/C.5。

### 路径 B：跑完整 DSH Web（需回装到 monorepo）
`apps/web` 依赖 workspace 协议与 cordis 宿主协议（`__DSH_BOOT__` 启动图），无法独立构建。
如需完整运行：把 `apps/web`、`packages/client` 移回 `deepseek-harness-master/` 对应位置后
`pnpm install && pnpm --filter @deepseek-ai/dsh-web-frontend exec vite build`。

## 备注
- 各包内 node_modules 已删（可由 pnpm install 重建）；lib/ 编译产物保留供路径 A 直接使用。
- `apps/web/tests/` 含 300+ E2E 用例与期望快照，是理解 DSH 交互契约的最佳材料。
