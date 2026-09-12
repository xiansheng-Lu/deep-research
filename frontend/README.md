# AI 研究者助手 - 前端工程

基于 Vue 3 + TypeScript + Vite 的单页应用，为 AI 研究者助手提供登录、研究发起、任务指挥舱、报告与分歧工作台等界面。

一次"研究运行（run）"在前端对应六个阶段的实时推进：意图分析 → 证据收集 → 证据评估 → 综合 → 报告起草 → 报告复核。运行过程中的状态通过 WebSocket 实时推送到指挥舱；证据冲突的分歧工作台为 M4 预留，当前仅占位。

当前处于 M1 联调阶段：M0 基础组件库、应用骨架与实时通道客户端已就绪，M1 的认证、项目、发起向导、指挥舱、报告页面已实装；内置 Mock 网关严格对齐 M1 冻结契约，可在后端缺失时独立完成页面联调，通过环境变量即可切换为真实后端。

## 技术栈

| 分类 | 选型 |
| --- | --- |
| 框架 | Vue 3.5（`<script setup>` + Composition API） |
| 语言 | TypeScript 5.6（strict 模式） |
| 构建 | Vite 5.4 |
| 状态管理 | Pinia 2 |
| 路由 | Vue Router 4（History 模式） |
| 浮层定位 | @floating-ui/dom |
| 自动化 | unplugin-auto-import、unplugin-vue-components |
| 规范 | ESLint 9 + Prettier 3 |
| 包管理 | pnpm 9.15.0（`packageManager` 字段锁定） |

## 环境要求

- Node.js >= 20.0.0
- pnpm 9.15.0（建议通过 corepack 启用：`corepack enable`）
- Windows 环境下推荐使用 PowerShell 执行本文档命令

## 快速开始

```powershell
# 安装依赖
pnpm install

# 启动开发服务器（默认对接 http://localhost:8000 的后端）
pnpm dev
```

开发服务器默认监听 `http://localhost:5173/`，配置为 `host: 0.0.0.0`、`strictPort: false`（5173 被占用时自动顺延端口）。

若后端未启动，页面请求会因代理失败而报错。此时有两种选择：

1. **启用内置 Mock 网关**（推荐用于纯前端开发，见下一节）；
2. 先启动后端服务，保持 [.env.development](.env.development) 中的 `VITE_MOCK=off`。

## 内置 Mock 网关

Mock 网关以 Vite dev 插件形式内嵌（入口 [src/services/mock/gateway.ts](src/services/mock/gateway.ts)），仅在 development 模式且 `VITE_MOCK=gateway` 时启用。启用后 dev server 不再配置代理，`/api/v1` REST 请求、`/healthz` 与 `/api/v1/ws` WebSocket 升级全部由插件在 Node 侧拦截处理。

### 启用方式

修改 [.env.development](.env.development)：

```
VITE_API_BASE=http://localhost:8000
VITE_MOCK=gateway
```

重新执行 `pnpm dev` 即生效。种子数据由 [src/services/mock/seed.ts](src/services/mock/seed.ts) 注入到内存存储 [src/services/mock/store.ts](src/services/mock/store.ts)，重启 dev server 后数据重置。

### 覆盖的通道与端点

- **REST**：由 [src/services/mock/router.ts](src/services/mock/router.ts) 分发，共 11 个端点，严格对齐 [openapi-m1.json](../docs/contract/openapi-m1.json) 冻结契约：`GET /healthz`、鉴权四端点（login/refresh/logout/me）、项目列表与创建、run 创建与详情、报告获取（`/runs/{run_id}/report` 与 `/reports/{run_id}` 两个等价路径）。
- **WebSocket**：`/api/v1/ws/runs/{run_id}/stream` 升级请求由 [src/services/mock/realtime.ts](src/services/mock/realtime.ts) 处理，握手前按 query 参数 `token` 鉴权（与后端一致，缺失或无效直接返回 403），run 产生的事件实时广播到对应频道，终态事件推送后关闭连接。

REST 与实时事件在 run 执行期间联动：每次脚本发出事件时，同步更新内存中的 run 状态并向 WS 通道广播；run 成功结束时在内存中落一份报告，供报告接口读取。

### 剧本（fixture）机制

run 的事件流不是随机生成的，而是由可编排的"剧本"驱动。M1 仅提供一个剧本 [fixtures/happy_path.ts](src/services/mock/fixtures/happy_path.ts)：六个阶段依次推进，无人工干预直至完成；`POST /api/v1/runs` 成功创建后自动执行该剧本，请求体不含 fixture 选择字段（冻结契约字段为 `project_id`、`question`、`tier`、`template_id`）。

剧本是一棵 `ScriptNode` 树，由 [src/services/mock/script/runner.ts](src/services/mock/script/runner.ts) 解释执行，M1 节点类型仅三种（见 [script/types.ts](src/services/mock/script/types.ts)）：

- `sequence`：顺序执行子节点；
- `emit`：延迟可选毫秒后发出一个实时事件；
- `wait`：等待固定时长。

需要新增联调场景时，按 happy_path 的写法构建新剧本，并在 run 创建路由中替换或登记即可，不要修改执行器；并行、人工中断、失败注入等节点类型随 M2+ 剧本扩展再加入 DSL。

Mock 模式的登录账号由 [src/services/mock/seed.ts](src/services/mock/seed.ts) 写入：邮箱 `demo@example.com`，接受任意非空密码。

## 环境变量

变量在 `.env.development` / `.env.production` 中配置，仅 `VITE_` 前缀的变量会暴露给客户端代码，配置加载逻辑见 [vite.config.ts](vite.config.ts)。

| 变量 | 开发默认值 | 生产默认值 | 说明 |
| --- | --- | --- | --- |
| `VITE_API_BASE` | `http://localhost:8000` | 空 | 后端 API 基址。非 Mock 模式下，dev server 据此代理 `/api`，并将 `http` 替换为 `ws` 派生 WebSocket 地址代理 `/ws` |
| `VITE_MOCK` | `off` | 不使用 | 设为 `gateway` 启用内置 Mock 网关，仅 development 模式有效 |

生产构建默认 `VITE_API_BASE` 为空，即前端与后端同源部署，由反向网关统一转发 `/api`、`/ws`。

## 常用脚本

| 命令 | 说明 |
| --- | --- |
| `pnpm dev` | 启动 Vite 开发服务器 |
| `pnpm build` | 先执行 `vue-tsc --noEmit` 类型检查，再产出生产构建到 `dist/` |
| `pnpm preview` | 本地预览生产构建产物 |
| `pnpm typecheck` | 仅执行 TypeScript / Vue 类型检查 |
| `pnpm lint` | ESLint 检查 `.vue` / `.ts` / `.tsx`，阈值为零警告 |
| `pnpm lint:fix` | ESLint 自动修复 |
| `pnpm format` | Prettier 格式化 `src/` 下的源码与样式 |

构建目标为 `es2020`，生产环境关闭 sourcemap；Vue 全家桶（vue / vue-router / pinia）被拆分为独立 chunk，单包告警阈值为 600KB。

## 与后端联调

### 代理规则

`VITE_MOCK=off` 时，Vite dev server 按以下规则代理（`changeOrigin: true`）：

| 前端路径 | 转发目标 |
| --- | --- |
| `/api`（HTTP） | `VITE_API_BASE` |
| `/api/v1/ws`（WebSocket 升级） | 同一条 `/api` 规则以 `ws: true` 转发至 `VITE_API_BASE` |

实时通道地址为 `/api/v1/ws/runs/{run_id}/stream`，与 REST 同属 `/api` 前缀，因此 WS 升级转发配置在 `/api` 规则上，不存在独立的 `/ws` 前缀规则。

### M1 契约与客户端生成

M1 阶段的 HTTP 契约已冻结在仓库的 [docs/contract/openapi-m1.json](../docs/contract/openapi-m1.json)（相对本目录为 `../docs/contract/openapi-m1.json`），包含 auth、projects、runs、reports 等端点的请求/响应 schema。

需要生成 TypeScript 客户端类型时，推荐使用 openapi-generator 的 typescript-fetch 生成器，例如：

```powershell
pnpm dlx @openapitools/openapi-generator-cli generate `
  -i ../docs/contract/openapi-m1.json `
  -g typescript-fetch `
  -o src/services/generated
```

生成产物建议放在 `src/services/generated/` 并在 `.gitignore` 中忽略，作为再生成资源维护；后端契约变更后重新执行该命令即可。当前 M1 手写的 REST 接口类型集中在 [src/services/api/types.ts](src/services/api/types.ts)，实时事件类型在 [src/services/realtime/types.ts](src/services/realtime/types.ts)，接入生成客户端时逐页面替换。

所有 HTTP 调用统一经 [src/services/http/http.ts](src/services/http/http.ts) 发起：它是 fetch 的薄封装，负责 `Accept` 与幂等键（`Idempotency-Key`）注入，并把两类后端错误响应归一为 `ApiError`（含 `status` / `code` / `title` / `detail` / `traceId`）：业务异常体 `{code, message, details}` 与 FastAPI 请求体校验错误体 `{detail: [...]}`，错误处理分支统一按 `code` 判断；401 时还会以 single-flight 方式刷新 access token 并重放原请求一次。SSE 流式接口（M2 闲聊 `/assistant/chat`）使用同模块导出的 `httpStream()` 获取原始 `Response`，鉴权与 401 重放口径一致，调用方自行按 `text/event-stream` 解析。

### M2 契约层与 M2-9 生成客户端切换清单

M2 在 OpenAPI 冻结前继续手写类型，枚举统一收敛在 [src/types/domain.ts](src/types/domain.ts)（单一事实源），由 [src/services/api/types.ts](src/services/api/types.ts) 再导出兼容 M1 引用路径；实体字段在文件头注释标注了后端出处（LLD §4.2 / 契约草案 §6 / 已落地的 M2-1 schema）。M2-1 意图与闲聊接口以后端 `feature/backend-m2-intent-router` 真实 schema 为准；看板（stages/sub-questions/evidence/cost）、HITL（pause/resume/intervene）、结构化报告（blocks/citations）字段在对应后端工作包（M2-4/5/6/7）冻结后逐批核对。

后端 M2-9 导出 openapi-m2 后，按以下清单切换到 typescript-fetch 生成客户端（预计 0.5-1 人日，独立工作包）：

1. 生成产物落 `src/services/generated/`（gitignore），以 openapi-m2.json 为输入。
2. 替换顺序：枚举（`types/domain.ts` 改为从 generated 类型收窄/别名）→ 实体接口（`services/api/types.ts`）→ 各 `services/api/*.ts` 请求函数改为 generated fetch 适配，保留现有函数签名，页面与 composables 零改动。
3. `http.ts` 的错误归一/401 单飞逻辑保留为适配层，不直接使用生成客户端自带错误处理。
4. SSE（assistant）与 WS（realtime）不在生成客户端覆盖范围，维持自研。
5. 切换完成后删除手写类型中已被生成产物覆盖的部分，并更新本小节。

## 目录结构

```
frontend/
├── index.html               # HTML 入口（含明暗主题防闪内联脚本）
├── vite.config.ts           # Vite 配置：别名、代理、Mock 插件、构建分包
├── src/
│   ├── main.ts              # 应用入口，装配 Pinia 与 Router，引入全局样式
│   ├── App.vue              # 根组件（RouterView 宿主）
│   ├── layouts/
│   │   └── AppShell.vue     # 全局外壳：顶部导航 + 受保护内容区
│   ├── views/               # 页面级视图，按业务域分目录
│   │   ├── auth/ home/ assistant/ wizard/
│   │   ├── project/ cockpit/ report/ disputes/
│   │   ├── templates/ knowledge/ account/ errors/
│   ├── components/ui/       # 自研基础组件库（无第三方 UI 框架）
│   │   ├── UiButton.vue UiCard.vue
│   │   ├── form/            # 输入类：Input/Textarea/Checkbox/Radio/Switch/Slider
│   │   ├── feedback/        # 反馈类：Badge/Tag/Toast/Skeleton/Empty/ErrorState
│   │   ├── nav/             # Tabs
│   │   └── overlay/         # 浮层类：Dialog/Drawer/Dropdown/Popover/Tooltip
│   ├── composables/         # 组合式函数（useDarkMode 等）
│   ├── stores/              # Pinia 状态（见下文"状态管理"）
│   ├── router/              # 路由表（index.ts）与登录守卫（guards.ts）
│   ├── services/            # 基础设施层
│   │   ├── http/            # fetch 封装与错误体归一（ApiError）
│   │   ├── realtime/        # WebSocket 客户端（心跳、指数退避重连）
│   │   ├── mock/            # 内置 Mock 网关（对齐 M1 冻结契约）
│   │   │   ├── gateway.ts   #   Vite 插件入口
│   │   │   ├── router.ts    #   REST 路由分发（11 个端点）
│   │   │   ├── realtime.ts  #   WS 升级与广播（query token 鉴权）
│   │   │   ├── store.ts seed.ts   # 内存存储与种子数据
│   │   │   ├── fixtures/    #   happy_path 剧本
│   │   │   └── script/      #   剧本节点 DSL 与解释执行器
│   │   ├── telemetry/       # 遥测事件采集与批量上报
│   │   ├── toast/           # 全局通知服务（配合 UiToastHost）
│   │   └── i18n/            # 简体中文文案
│   ├── styles/              # tokens.css 设计令牌 / theme.css 明暗主题 / base / utilities
│   └── types/               # 自动生成的 auto-imports.d.ts 与 components.d.ts
└── public/                  # 静态资源（favicon.svg）
```

## 路由一览

公共路由无需登录；其余路由经 [src/router/guards.ts](src/router/guards.ts) 校验会话：未登录时跳转 `/auth/login` 并携带 `redirect` 查询参数，登录后回跳。守卫同时负责按路由 `meta.title` 设置文档标题。

| 路径 | 视图 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| `/auth/login` | LoginView | 公共 | 登录 |
| `/home` | HomeView | 受保护 | 首页 |
| `/assistant` | AssistantView | 受保护 | AI 助手 |
| `/projects` | ProjectListView | 受保护 | 项目列表 |
| `/projects/:projectId/tasks` | ProjectTasksView | 受保护 | 项目任务 |
| `/projects/:projectId/runs/:runId/cockpit` | CockpitView | 受保护 | 研究运行指挥舱 |
| `/projects/:projectId/runs/:runId/report` | ReportView | 受保护 | 研究报告 |
| `/projects/:projectId/runs/:runId/disputes` | DisputesView | 受保护 | 分歧工作台 |
| `/wizard` | WizardView | 受保护 | 发起研究向导 |
| `/templates` | TemplateLibraryView | 受保护 | 模板库 |
| `/knowledge` | KnowledgeView | 受保护 | 知识库 |
| `/account` | AccountView | 受保护 | 账户设置 |
| `/account/team` | AccountTeamView | 受保护 | 团队管理 |
| `/` | - | - | 重定向到 `/home` |
| 其余任意路径 | NotFoundView | 公共 | 404 |

项目边界（越权访问他人项目）不在路由层区分 403，统一由资源接口按 404 处理。

## 状态管理

Pinia store 位于 [src/stores/](src/stores/)，均采用 Composition 风格（setup store）：

| Store | 职责 |
| --- | --- |
| `session` | 登录态与当前用户。access token 仅内存持有（刷新页面即失效，需重新登录或用 refresh token 换新）；refresh token 持久化在 localStorage 的 `refresh_token` 键 |
| `project` | 项目实体缓存与当前项目指针（分页列表 + upsert），作为项目边界判断依据 |
| `team` | 当前团队信息与成员 |
| `ui` | 纯界面状态（主题、侧边栏等跨页面 UI 开关） |

退出登录时调用 `session.clear()` 清空 token 与本地持久化。

## 实时通道

M1 实时通道统一为 WebSocket（SSE 已在 M1 契约对齐时移除，M2+ 如恢复再扩展）。[src/services/realtime/realtime.ts](src/services/realtime/realtime.ts) 提供单例式 `RealtimeClient`，按 channelId 持有各自的 `WsChannel`：

- **鉴权**：JWT 由 URL query 参数携带（`/api/v1/ws/runs/{run_id}/stream?token=<jwt>`），地址由 [src/services/api/runs.ts](src/services/api/runs.ts) 的 `buildRunStreamUrl` 构造，与后端 `ws.py` 口径一致；
- **心跳**：服务端每 30s 发送 ping，客户端兜底每 25s 主动 ping 防止反向超时；
- **重连**：非主动关闭时指数退避重连，初始 1s、上限 30s；收到终态事件（`run.finished` / `run.failed`）后不再重连；
- **事件编排**：通道记录 `lastEventId`；[src/composables/useRunStream.ts](src/composables/useRunStream.ts) 已实装 M1 编排——进入页面先 `GET /runs/{run_id}` 取初帧并映射六阶段，非终态建立 WS 订阅 `stage.started` / `run.finished` / `run.failed`，每次连接 live（含重连）后补一次 REST，对齐订阅空窗漏帧。

## 主题与样式

- 明暗主题通过 `<html>` 上的 `data-theme`（`light` / `dark`）与 `data-theme-mode`（`system` / `light` / `dark`）切换；
- [index.html](index.html) 内联脚本在应用挂载前读取 localStorage 的 `themeMode` 并解析系统偏好，提前写入属性，避免主题闪烁；切换逻辑封装在 [src/composables/useDarkMode.ts](src/composables/useDarkMode.ts)；
- 样式分三层：[tokens.css](src/styles/tokens.css) 设计令牌（颜色/间距/圆角等变量）、[theme.css](src/styles/theme.css) 明暗主题取值、[base.css](src/styles/base.css) 与 [utilities.css](src/styles/utilities.css) 基础样式与工具类。组件只引用令牌，不硬编码颜色值。

## 开发约定

- **路径别名**：`@/` 指向 `src/`，由 [vite.config.ts](vite.config.ts) 与 [tsconfig.json](tsconfig.json) 共同保证。
- **自动导入**：Vue / Vue Router / Pinia 的 API 由 `unplugin-auto-import` 自动注入；`src/components/` 下组件由 `unplugin-vue-components` 按需自动注册，均无需手写 import。对应的 `.d.ts` 生成在 `src/types/`，ESLint globals 由 `.eslintrc-auto-import.json` 提供，这两类生成文件提交到仓库以保证类型检查独立可跑。
- **TypeScript**：开启 `strict`、`noUnusedLocals`、`noUnusedParameters` 等严格选项；`pnpm build` 会先做全量类型检查。
- **提交前自检**：`pnpm lint` 与 `pnpm typecheck` 必须通过。
- **注释与编码**：注释统一使用简体中文，文件以 UTF-8 保存（注意 Windows 下 PowerShell `Set-Content` 默认编码问题，Mock 层已对 BOM 做容错）；代码中不使用 emoji 或装饰性字符。
- **不重复造轮子**：新增组件或服务前先检索 `components/ui/` 与 `services/` 下的既有实现，优先在现有实现上修改扩展。
