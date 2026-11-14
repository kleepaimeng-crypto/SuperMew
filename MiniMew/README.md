# MiniMew：从零复现 SuperMew

MiniMew 是以当前 SuperMew 为参考的分阶段学习与复现计划。目标是理解并逐步实现知识库 Agent 的执行、检索、恢复和评测机制。

本目录包含独立的 FastAPI 后端、Vue 聊天前端、文档入库基础与学习文档，使用自己的依赖锁文件和虚拟环境，不接入上级 SuperMew 的执行链。学习进度统一见交接文档。

换设备先读 [当前检查点与续接决定](AGENT_HANDOFF.md#当前检查点) 和 [换设备与同步](AGENT_HANDOFF.md#换设备与同步)：其中保存已确认选择、下一步范围、恢复 Agent 的提示及本机配置/数据的迁移边界。具体代码职责见对应章节实施记录，不依赖旧聊天或本机 memory。

理解整体设计先读[架构导读](ARCHITECTURE_GUIDE.md)：以三个协作系统串起运行进程、模块调用、数据归属、技术选择与后续连接点。教学按最新约定聚焦架构与模块关系，不再默认逐轮提简单场景或异常判断题。

## 运行最小后端

需要 Python 3.12、uv 和正在运行的 Docker Desktop（Linux containers）。在 PowerShell 中进入本目录后运行：

```powershell
cd D:\Project\SuperMew\MiniMew
uv sync --dev --locked
# 首次使用才复制；已有 .env 时不要覆盖。
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# 编辑 .env，填写独立的 DB_PASSWORD、REDIS_PASSWORD、JWT_SECRET（至少 32 字符），均带 MINIMEW_ 前缀。
docker compose up -d --wait
uv run --env-file .env --no-sync alembic upgrade head
uv run --env-file .env --no-sync uvicorn minimew.app:create_app --factory --host 127.0.0.1 --port 8001
```

换设备时将路径改成实际克隆位置。uv 会创建本目录的 `.venv` 并安装 `src/minimew` 包，无需手动设置 PYTHONPATH。不要在上级目录执行这些命令，否则会选择上级项目。端口冲突时改启动参数；不要关闭未知进程。

访问 `http://127.0.0.1:8001/health/live`，默认响应：

```json
{"status":"ok","app_name":"MiniMew"}
```

接口只表示 HTTP 服务存活，不证明数据库、模型或聊天业务就绪。交互式 API 文档位于 `/docs`，按 Ctrl+C 停止服务。

`/health/ready` 执行有超时限制的 `SELECT 1`，成功返回 200 和 `{"status":"ok","database":"ok"}`；数据库不可用返回 503 和 `{"status":"unavailable","database":"unavailable"}`。它不检查业务表迁移、Redis 或模型服务。配置正确但数据库停机时，应用仍能启动并响应 live；缺失密码等非法配置则直接拒绝启动。

### 独立数据库与迁移

Compose 项目为 `minimew`，数据库/账号默认 `minimew`，宿主机端口 `127.0.0.1:55432`，数据卷 `minimew_postgres_data`。使用官方 PostgreSQL 18 镜像并锁定 digest；PostgreSQL 18 数据卷挂载到 `/var/lib/postgresql`。不复用 SuperMew 的数据库、网络或数据卷。

本机开发配置使用容器初始化账号，拥有管理权限；生产部署时需另行区分迁移账号与最小权限运行账号，本步骤不是生产部署方案。密码保存在 Git 忽略的 `.env`，配置对象用 SecretStr 隐藏表示，响应不包含 DSN 或异常详情。

```powershell
uv run --env-file .env --no-sync alembic current
docker compose stop postgres
docker compose up -d --wait
```

`alembic upgrade head` 是显式迁移命令，API 启动不执行迁移。版本链为 `0001`（空起点）→ `0002`（users、threads）→ `0003`（用户登录凭据）→ `0004`（刷新凭据记录）→ `0005`（runs、messages 与 Thread.last_sequence）→ `0006`（模型配置与不可改写快照）→ `0007`（执行生命周期与 Journal）→ `0008`（正文分片与失败前缀保护）→ `0009`（Event/Outbox 原子保存）→ `0010`（取消请求与取消终态）→ `0011`（个人知识库、文档版本、索引任务与提交映射）。`alembic current` 应显示 `0015 (head)`。0015新增Run私有Checkpoint/一次性恢复接收记录及等待/待恢复状态约束，保留已有Run、消息、引用、快照与Journal；升级后须重启配套API和Worker。0014扩展持久工具观测类型及guard、保留旧Journal；0013新增答案依据快照及Message/Run关联约束，旧消息不补造引用；0012新增不可变索引产物、清理账本和Worker心跳，支持staged重新领取与有界重试；保留0011已有文档和Job。0005 为已有 Thread 增加默认 0 的序号，0006 原样保留历史 Run/消息，不为旧任务补猜模型；0007 保留已有 pending 和消息，不补造旧事件；0008 原样保留消息与事件，允许后续追加正文分片，并保护失败正文与已提交分片一致；0009 为新事件增加同事务通知待办，不改写历史事件或回填历史通知；0010 保留旧 Run/快照/正文/事件/Outbox，旧取消时间为 NULL，并允许未领取就取消。更早版本仍受下述旧用户检查限制。0011 只新增文档域表与约束/trigger，原用户、对话、Run/消息/事件与模型配置原样保留；版本内容和构建快照不可改写，任务领取使用独立 lease/fence。ORM 的 `Base.metadata` 已接入迁移环境，`uv run --env-file .env --no-sync alembic check` 可检查结构漂移。自动生成的迁移仍需人工检查。此异步迁移环境参照 [Alembic 官方说明](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic)。

0003 在锁定 users 表后检查旧数据：只要存在无凭据旧用户就拒绝升级，事务回滚保留用户、对话和迁移版本，不生成默认密码。出现 `Existing users require an explicit credential migration plan` 时停止，先明确处理旧账号的方案，不自行删库或清用户。0003 禁止 downgrade，避免静默丢弃登录凭据；有数据的环境需要经过审查的恢复方案。

### 个人用户与对话模型

| 表 | 字段与约束 |
| --- | --- |
| `users` | UUID 主键 id、带时区的 created_at、唯一且非空 username、非空 password_hash |
| `threads` | UUID 主键 id、必填 user_id 外键、必填 title（最多 200 字符）、带时区的 created_at |
| `refresh_tokens` | token_hash 主键、user_id 外键及索引、created_at、expires_at、可空 revoked_at；无原始令牌 |

UUID 和创建时间由 PostgreSQL 默认值生成，直接 SQL 写入也适用。ORM 在 INSERT/flush 后取得这些值；flush 执行 SQL 但不等于 commit。title 列允许空字符串，正式接口创建时写入“新对话”，首次保存用户消息时更新标题。`ix_threads_user_id` 支持按用户查询；外键 RESTRICT 拒绝删除仍有对话的用户，不自动级联删除。

本版按个人用户设计，已包含登录认证和 Thread 业务接口，不含 Tenant。外键只验证用户存在，不阻止越权查询；正式 Thread/Run 接口同时限制资源 ID 与服务端验证的 current_user_id。

### 用户名密码认证

| 接口 | 输入/凭据 | 成功响应 |
| --- | --- | --- |
| `POST /auth/register` | JSON username、password | 201：id、username、created_at，不返回密码或哈希 |
| `POST /auth/login` | JSON username、password | 200：access_token、token_type=bearer、expires_in（秒） |
| `GET /auth/me` | `Authorization: Bearer <access_token>` | 200：当前用户公开信息 |
| `POST /auth/refresh` | 浏览器自动携带刷新 Cookie，允许空 body | 200：新 Access Token，并通过 Set-Cookie 轮换 Refresh Token |
| `POST /auth/logout` | 浏览器自动携带当前刷新 Cookie，允许空 body | 204：撤销当前凭据并清除 Cookie |

用户名先去除首尾空白并转小写，然后要求 3–32 个 ASCII 字母、数字或下划线；因此 Alice 和 alice 是同一个用户名。注册密码 12–128 字符，不裁剪、不改大小写；登录只验证输入，不强制套用注册的最小长度。请求不能额外指定 id、password_hash 等字段。

新增 [argon2-cffi](https://argon2-cffi.readthedocs.io/en/stable/howto.html) 生成带随机盐的 Argon2id 哈希，并在显式线程池中计算和验证。不存在的用户名也验证一次虚拟哈希，错误用户名和错误密码统一返回 401。重复注册由数据库唯一约束保证（包括并发），返回 409。非法请求返回 422 通用错误，不回显 password、原始 JSON 或校验 input。

[PyJWT](https://pyjwt.readthedocs.io/en/stable/api.html) 固定使用 HS256；令牌包含 sub、iat、exp、iss、aud、jti 和 token_type。后端固定校验算法、签名、有效期、签发方、受众和 access 用途，再查询用户是否存在。不能通过仅解码 payload 获取可信身份。令牌响应设置 no-store，不持久化原始 Token。损坏的数据库哈希、数据库故障不会伪装成密码错误。

`MINIMEW_JWT_SECRET` 必填，至少 32 字符，应使用独立随机密钥，不能复用数据库密码。`MINIMEW_ACCESS_TOKEN_MINUTES` 默认 15，允许 1–60。修改密钥后需重启，新密钥无法验证旧令牌。本机生成的密钥仅保存在忽略的 `.env`，不要将其贴进聊天或提交到 Git。

### 登录续期与退出

登录成功后，Access Token 在 JSON 中返回；Refresh Token 仅通过 `minimew_refresh` Cookie 传输，不出现在 JSON。注册仍只创建用户，需要随后登录。Refresh Token 使用 32 字节随机数生成，数据库保存 SHA-256 摘要，不保存原始值；它不是 JWT。高熵随机凭据可用摘要查找，与用户密码的 Argon2id 哈希用途不同。

Cookie 固定为 `HttpOnly; Path=/auth; SameSite=Lax`，不设置 Domain。`MINIMEW_REFRESH_TOKEN_DAYS` 默认 7 天、范围 1–30；每次成功刷新都签发有效期从本次刷新起计算的新凭据。`MINIMEW_REFRESH_COOKIE_SECURE` 本机 HTTP 默认为 false，production 配置必须为 true，缺失则拒绝启动；HTTPS 部署也需正确配置代理。Cookie 设置和清除使用相同 Path 与安全属性，参见 [Set-Cookie 说明](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie)。

刷新在一个事务内撤销旧凭据、保存后继摘要并签发新的 Access Token，成功提交后才发送 Cookie。统一按 User → RefreshToken 行锁顺序处理登录签发、轮换、退出和重放撤销，并在取得锁后读取数据库时间判断过期。

- 有效凭据：轮换，返回 200。
- 自然过期：仅拒绝本次请求，不撤销其他凭据；过期判断优先于已撤销判断。
- 尚未过期但已撤销：视为重放，撤销该用户所有仍有效的 Refresh Token；先提交撤销事务，再返回 401。
- 缺失、畸形、未知、已过期或重放：统一返回 401 并清除请求端刷新 Cookie，错误不包含凭据。
- 退出：只撤销 Cookie 指向的凭据，重复退出或没有 Cookie 也返回 204 并清 Cookie，不额外撤销其他设备。

撤销刷新凭据不会即时撤销已签发的 Access Token。前端退出时清空内存 Access Token，并先等待正在进行的刷新完成，再向服务器退出最新 Cookie。若刷新先完成而退出仍携带旧 Cookie，服务端不会凭空把退出目标改成新凭据；若随后复用旧凭据刷新，则会触发重放处理。两个同时使用同一 Refresh Token 的请求只有一个成功，另一个触发重放并撤销后继，因此前端协调并发刷新。当前没有退出所有设备接口。

认证 POST 加入同源检查：Origin 优先，其次 Referer；外源、null Origin 或无可信来源的 same-site/cross-site Fetch Metadata 返回 403，不修改凭据。无浏览器来源元数据的本机 CLI 可以使用。本步不提供跨源 Cookie allowlist。认证的正常响应和可处理错误均 no-store。登录限流、完整入口流量治理、生产部署与刷新记录定期清理尚未实现；已撤销记录保留用于重放判断，不在请求中删除。

后续浏览器只能在内存保存 Access Token，不存 localStorage/sessionStorage 或 URL。Refresh Cookie 有效时，页面重新加载可调用 refresh 恢复内存 Token；刷新失败需重新登录。

手动验证：按启动命令运行新服务，打开 `http://127.0.0.1:8001/docs`，先调用 register，再调用 login；将返回的 access_token 填入 Authorize 的 Bearer 输入，调用 me。然后调用 refresh，浏览器自动携带并替换 Cookie；返回的新 Access Token 可更新到 Authorize。调用 logout 后再次 refresh 应为 401；Swagger 中旧 Access Token 不会自动清除且未过期时仍可能访问 me。不要持久化令牌。旧进程需要 Ctrl+C 后重启，不会因代码落盘自动切换。

停止容器保留数据。不要用 `docker compose down -v` 做日常停止，它会删除数据卷。PostgreSQL 初始化变量只对空数据卷生效，修改 `.env` 中的密码不会自动修改已有数据库密码。

### 配置与代码入口

| 文件 | 职责 |
| --- | --- |
| `pyproject.toml` / `uv.lock` | 独立依赖声明与解析结果；锁文件由 uv 生成 |
| `src/minimew/app.py` | 应用工厂：校验配置、保存到应用实例、注册路由 |
| `src/minimew/settings.py` | 配置模型及 Depends 所用的配置读取函数 |
| `src/minimew/database.py` | Engine、连接 URL、请求独立 AsyncSession |
| `src/minimew/models.py` | Base metadata、用户、对话、Run/Message 与模型配置 ORM 映射 |
| `src/minimew/model_control.py` | 配置校验、当前选择、版本更新、冻结和读取 Run 快照 |
| `src/minimew/model_cli.py` | 本地 set/show/show-run 管理命令 |
| `src/minimew/execution/` | 执行编排、领取/结果事务、上下文整理和 Journal 追加，职责见下文 |
| `contracts/run_event_v1.json` | MiniMew 事件、状态与错误码真源；独立于上级同名契约 |
| `scripts/generate_event_contract.py` | 从事件真源生成并核对 Python/TypeScript 消费者 |
| `src/minimew/security.py` | 密码哈希、签发和校验 Access Token |
| `src/minimew/api/auth.py` | 注册、登录、CurrentUser 依赖、me 与公开响应模型 |
| `src/minimew/refresh.py` | 刷新凭据签发、行锁、轮换、重放撤销和退出事务 |
| `src/minimew/auth_http.py` | Cookie 属性、同源入口检查与 no-store 响应头 |
| `src/minimew/api/health.py` | 存活与数据库就绪接口 |
| `compose.yaml` / `docker/redis-entrypoint.sh` | 独立 PostgreSQL/Redis 与数据卷，Redis 使用哈希 ACL 启动 |
| `alembic.ini` / `migrations/` | 独立迁移环境与版本链 |
| `tests/test_app.py` | 接口、配置隔离与非法配置测试 |

应用环境变量：`MINIMEW_APP_NAME`（默认 MiniMew，不能为空）和 `MINIMEW_ENVIRONMENT`（development/test/production，默认 development）。数据库配置见 `.env.example`：HOST、PORT、NAME、USER、PASSWORD、TIMEOUT_SECONDS，均以 `MINIMEW_DB_` 为前缀；密码必填，超时默认 3 秒。环境名称目前仅用于配置校验，并不自动开启认证或生产安全设置。配置只在创建应用时读取，修改后需要重启。 Redis 配置同样见 `.env.example`：独立密码必填；默认地址为 `127.0.0.1:56379`，key 前缀为 `minimew:run_events:v1`。

```powershell
$env:MINIMEW_APP_NAME = "MiniMew Local"
$env:MINIMEW_ENVIRONMENT = "development"
uv run --env-file .env --no-sync uvicorn minimew.app:create_app --factory --host 127.0.0.1 --port 8001
```

非法配置会抛出 ValidationError，使应用启动失败。应用本身不会自动加载 `.env`；以上命令通过 uv 的 `--env-file .env` 显式注入本目录配置。Compose 默认读取本目录 `.env`。同名终端环境变量优先，遇到配置不一致时先检查当前终端，不输出密码。设置环境变量使用 [pydantic-settings 的配置机制](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)。

应用 lifespan 创建 Engine、Session 工厂及 Redis 资源，并管理通知扫描任务；退出先停通知任务、关闭 Redis，再 dispose 数据库连接池。模块 import 不创建连接或启动循环。`get_session` 使用 `yield` 提供请求自己的 Session，结束时关闭并回滚未提交事务，不自动 commit。后续写业务时用 `async with session.begin():` 明确事务范围。并发任务需要各自的 Session，参见 [SQLAlchemy 异步会话说明](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)。

### 本地验证

先按 [AI 编程测试策略](AGENTS.md#ai-编程测试策略) 分析影响范围，再选择相关测试文件或用例。以下是整套离线检查入口，不要求每次改动全部执行；纯文档更新通常只需差异、链接和格式检查。

```powershell
uv run --no-sync pytest -q
uv run --no-sync ruff check src tests migrations scripts
uv run --no-sync ruff format --check src tests migrations scripts
```

pytest 测试不访问模型、数据库或外网。真实数据库检查需先启动容器并完成迁移，再显式运行：

```powershell
uv run --env-file .env --no-sync python scripts/check_database.py
# 可选故障检查：短暂停止本项目数据库，finally 中恢复；不要与业务操作同时运行。
uv run --env-file .env --no-sync python scripts/check_database.py --stop-database
```

该脚本仅接受默认 MiniMew 本机地址与数据库，检查 0010 revision、预期表集合、临时表事务提交/回滚、Session 清理、真实 ready 响应。故障检查恢复后，同一个应用应重新返回 ready 200。

模型与迁移的真实 PostgreSQL 验证单独执行：

```powershell
uv run --env-file .env --no-sync python scripts/check_models.py
```

它需要本地开发账号的建库权限，创建随机 `minimew_test_<uuid>` 临时库，验证空库迁移、旧无凭据用户时原子拒绝升级、已有注册用户/Thread/无快照 Run 及消息升级保留、重复升级、metadata 一致性、ORM/外键/删除保护与索引。还显式运行 auth、refresh、threads、runs 的 PostgreSQL 测试，验证认证、并发轮换、Thread 隔离、Run 幂等与回滚、模型配置更新、快照不可改写、配置更新与预留的并发以及本地管理命令；完成或失败后清理本次临时库。它不会在开发库降级或清空开发数据。pytest 默认跳过这些真实数据库测试，不要给它随意设置 MINIMEW_INTEGRATION_DB。

依赖下载需要联网。若受限环境无法写入默认缓存/临时目录，可在本目录先执行以下配置，再执行 uv 命令：

```powershell
New-Item -ItemType Directory -Force .artifacts/tmp | Out-Null
$env:UV_CACHE_DIR = "$PWD\.artifacts\uv-cache"
$env:TEMP = "$PWD\.artifacts\tmp"
$env:TMP = $env:TEMP
```

这些环境变量只影响当前终端及其子进程；`.artifacts` 和 `.venv` 不纳入 Git。

## 运行聊天前端

先按上文启动数据库和后端（8001），另开 PowerShell：

```powershell
cd D:\Project\SuperMew\MiniMew\frontend
npm ci
npm run dev
```

访问 `http://127.0.0.1:5174`。前端固定监听本机 5174，端口被占用时拒绝启动，不自动换端口。Node 要求 `^22.12.0 || >=24.0.0`；本次复用 Node 24.18.0、npm 11.16.0，安装独立 node_modules。新设备不要复制 node_modules。

页面支持注册、登录、账户信息、退出与刷新页面恢复。注册成功后仍需登录。登录后点击顶部“对话”或账户页“进入我的对话”，访问 `/threads`：左侧显示当前用户的列表，可新建、刷新列表、加载更多；选中后地址变为 `/threads/{id}`，刷新浏览器会恢复身份并重新查询该对话。右侧支持消息历史、加载更早消息、发送问题、流式回答、Run 状态、重连和按 Run 停止；刷新后重新查询历史和恢复未结束的执行。首次提问提交后，列表标题跟随后端更新。前端使用 Vue 3、TypeScript、Pinia、Vue Router、Vite，HTTP 使用原生 fetch，不额外安装 Axios。

- `src/api.ts`：统一 JSON 请求、超时、公开错误文案和响应字段检查；不自动重试写请求。
- `src/stores/auth.ts`：唯一认证状态，Access Token 仅在内存；同标签恢复共享 Promise，支持 Web Locks 的同源标签页串行操作 Cookie。登录/退出用 generation 阻止旧响应重新写入账户。
- `src/router.ts` 和 `App.vue`：等待恢复后再显示受保护页面。401 表示未登录；网络错误、超时、500 或畸形响应显示重试，不自动跳登录。
- `src/stores/threads.ts` 和 `views/ThreadsView.vue`：管理列表、分页和选中对话。退出或身份变化立即清空；快速切换只接受最后一次详情请求。创建提交得到成功响应后才加入列表，不生成本地假 ID；重复点击只提交一次，不自动重试 POST。响应丢失时创建可能已成功，应先刷新列表确认。分页按 ID 去重，但继承后端 offset 翻页期间可能发生位移的边界，可刷新列表重新对齐。
- `src/chat.ts`、`stores/chat.ts`：校验正式 Message/Run 响应，加载历史与分页，保留提交意图、管理每个 Run 的投影及取消交互；切换 Thread/账号时中止旧连接并拒绝迟到响应。创建 Run 已成功但加载历史失败，只重试历史。
- `events/runEventStream.ts`、`runEventContract.ts`、`runEventReducer.ts`：分别负责 fetch SSE 读取与帧解析、按 JSON Schema 真源校验、连续事件投影。每次重连从最后连续处理成功的序号补读；历史完整正文不重复追加重放分片。SSE 单独采用 45 秒闲置预算，同一页面最多同时订阅两个 Run，其余等待空位后重放。
- `components/ChatPanel.vue`：展示持久消息、流式正文、状态、输入与恢复入口。停止请求的 202 不代表已经取消，界面等待持久终态。关闭页面只断开本地连接。
- 聊天接口/SSE 遇到 401 时共享一次 Refresh，保留原 Run、正文和游标；Refresh 明确返回 401 才清空身份并进入登录页，服务异常保留恢复入口。Thread 管理原有 401 提示/手动恢复路径保留；与聊天刷新重叠的同用户旧 Token 请求可用新 Token 重试一次。404、403 或非法对话地址统一显示不可访问，不泄露其他用户归属。
- HttpOnly Refresh Cookie 由浏览器管理。localStorage/sessionStorage 均不保存凭据。sessionStorage 保存非凭据退出标记，以及按用户/Thread 隔离的未知提交意图（原问题正文和幂等键）；刷新后可用原键重试，明确成功或退出/换账号后清除。存储不可用时仍支持当前页面内重试。退出失败后刷新页面优先重试退出，不悄悄恢复登录。
- Vite 将 `/auth` 和 `/v1` 代理到 `http://127.0.0.1:8001`，保留 Host（changeOrigin=false），保持后端 Origin 校验。修改后端端口可在启动 Vite 前设置 `$env:MINIMEW_API_TARGET = 'http://127.0.0.1:新端口'`。不要通过绕过同源检查或随意放开 CORS 解决代理问题。

边界：没有 Web Locks 的浏览器仅保证单标签页并发协调；跨标签退出不会立即清空其他标签已有的内存 Access Token，它仍受后端有效期约束。未提供后台定时续期、任意业务接口的全局自动重试或跨账号业务缓存；聊天链路采用上述显式身份恢复。消息目前按纯文本显示。Vite 代理用于本地开发，生产同源部署尚未实现。

认证和 Thread 类型来自已有后端路由，修改接口后在 MiniMew 目录运行：

```powershell
uv run --no-sync python scripts/export_frontend_contract.py
cd frontend
npm run contracts
```

`openapi.json` 和 `src/generated/api.d.ts` 是生成物，不手改。导出不读取 Secret、不访问数据库；生成的 TS 类型不替代 `api.ts` 的运行时检查。

前端检查（在 frontend 目录）：

```powershell
npm test
npm run build
npm run format:check
npm audit --omit=dev --audit-level=high
# 首次使用需要显式安装测试浏览器，会联网下载。
npx playwright install chromium
npm run test:e2e
```

默认浏览器测试使用路由模拟，不依赖模型或数据库；真实流程默认跳过。测试前端固定使用 5175，不占用开发用的 5174。在 MiniMew 目录显式执行以下命令，会创建随机临时数据库、在 8002 启动临时后端并运行真实浏览器验证，最后清理自己创建的数据库与后端，不重启已有 8001/5174 服务。脚本不会停止占用测试端口的其他进程；临时后端启动日志保存在忽略的 `.artifacts/frontend-test-backend.log`。

```powershell
uv run --env-file .env --no-sync python scripts/check_frontend.py
```

第三章聊天集成检查使用以下独立入口（同样在 MiniMew 目录执行）：

```powershell
uv run --env-file .env --no-sync python scripts/check_chat_frontend.py
# 只运行新增聊天场景：
uv run --env-file .env --no-sync python scripts/check_chat_frontend.py tests/chat-live.spec.ts
```

该入口要求本机独立 PostgreSQL 可用，创建并迁移自己的随机临时库，在随机本机端口启动自有 API，通过测试 Vite 的 5175 端口验证真实认证、Run/Message、Journal/SSE、取消、失败和刷新恢复。模型输出由受控 Provider 提供，不使用真实密钥或调用 DeepSeek；Redis 通知在该测试夹具中关闭，验证正式 Journal 定时补读。默认还执行已有真实认证/Thread 用例。测试结束清理自己的库和进程，日志保存在忽略的 `.artifacts/chat-browser-*/backend.log`；已有服务和开发库不作为测试写入目标。不要手工设置测试开关连接开发库。真实模型及 Redis 故障检查仍按第二章独立入口执行，本次不将历史结果计为新验收。

第三章职责地图和本轮验证范围见 [结章总结](chapters/03-最小聊天前端.md#结章文字总结2026-11-09)。


## Hybrid 构建与显式升级（6.2）

新知识库默认 `hybrid-jieba-bm25-v1`，可用 `MINIMEW_DOCUMENT_INDEX_LAYOUT=dense-cosine-v1` 显式选择 Dense 基线。已有知识库不会随环境变量重写。旧 Dense 产物只能显式报告该能力；网络和存储故障不会伪装成 Dense 降级或无资料。

在 MiniMew 目录通过本地管理权限指定用户，先查看计划，再执行：

```powershell
uv run --env-file .env --no-sync python -m minimew.documents.upgrade --user <user_uuid>
uv run --env-file .env --no-sync python -m minimew.documents.upgrade --user <user_uuid> --apply
```

升级更新该用户未来上传的构建目标，经既有候选Version/IndexJob/Worker核验发布；旧current在成功前继续可读，源文件和历史引用不改。存在并发上传时拒绝覆盖；批量可能部分入队，重新执行可继续，不自动清库或重建集合。独立Worker同时识别旧Dense和新Hybrid的显式构建身份。API/Worker需加载最新代码，普通API不执行迁移或索引构建。

## 本地模型与 RAG 策略（6.3–6.7）

### Run 内材料评判（6.4）

新Run默认冻结v4策略（沿用v3共享推理额度）：同一Provider承担回答与内部材料评判，先对实际有预算草稿评判，再原子登记Evidence。`MINIMEW_REASONING_ENABLED=false`只让之后新Run冻结v2；历史Run继续自身快照。该开关不代替`MINIMEW_KNOWLEDGE_ENABLED`，不下载模型。

v3最多12次模型尝试（含重试）、外层4次、工具6次、检索8次；内部最多并发2并保留最终回答的一次额度，共用原Profile总期限。Grader/Planner/Rewrite输出上限分别1024/512/256 token且不超过Profile上限。内部JSON和中间文字不进入公开Journal，最终答案继续原引用校验与持久化链。详见[第六章实现记录](chapters/06-完整RAG策略.md#64-实施记录2026-11-14)。



### 单次改写与图编排（6.5）

知识工具内部使用进程内LangGraph：先评判实际材料，不足时全Run最多一次Step-back补查，再按原用户问题复评。检索式可变，用户Message、固定scope、Run和最终回答目标保持；仅最终材料一次登记Evidence。图无持久checkpointer，不支持节点恢复（第七章范围）。

### 有限拆解与聚合（6.6）

材料不足且存在多方面缺口时，Planner全Run最多一次拆成2–4子问题，并发2复用固定scope与共享期限/额度；按计划顺序去重选材，再按原用户问题评判实际保留材料。部分失败明确返回不足/稳定失败码，全部子任务失败则工具失败。子任务不会创建业务Message或独立答案；原Run仍只在持久终结事务宣布完成。离线/真实PG入口分别为`tests/test_reasoning*.py`和`check_runtime.py tests/test_reasoning_pg.py`，真实CPU/Milvus受控链用`scripts/check_knowledge.py`；不代表收费模型质量或节点恢复能力。

### 固定案例与结章对照（6.7）

```powershell
uv run --no-sync pytest -q tests/test_rag_cases.py
uv run --env-file .env --no-sync python scripts/check_rag_suite.py
# 显式更新共同观察资产；默认只写忽略的完整报告
uv run --env-file .env --no-sync python scripts/check_rag_suite.py --write-repair-observation
```

使用[固定资料](examples/rag/chapter06-policy.md)/[案例](examples/rag/chapter06-cases.json)，临时PG、独立Worker发布及自有Milvus scope，比较实际预算后材料，并沿原Run验收受控评分/改写/拆解/失败/额度与引用。默认每路线3样本，`--samples 1`只做单次检查；不报告可靠p95。共享[观察资产](examples/rag/chapter06-observation.json)不含设备时延/身份，不是生产质量Gate；真实DeepSeek质量与线上时延未测，收费0。

本次编号/跨章节例的Hybrid扩大必要材料覆盖，但拆解例聚合后可从3/3降至2/3，受控“足够”不能消除原文缺口。详见[结章总结与代码框架](chapters/06-完整RAG策略.md#结章文字总结2026-11-14)。内存受限时需先检查进行中任务，再安排本地模型并存；显式验收不会自动停止开发服务。

### 执行异常反馈（第六章后专项）

失败回答显示中文原因，可展开错误码和任务编号；工具过程也展示tool.failed原因。实时SSE与刷新后的持久Run共用映射，连接中断、引用/过程读取故障及取消保持各自状态。同步历史不会重跑旧任务；提示重新提问时会创建新任务。

服务端`execution_diagnostic`记录Run、阶段、稳定reason和安全堆栈（文件/函数/行号），不记录异常文本、源码行、locals、原始凭据或模型正文。`execution_failure_handled`表示失败已处理，持久终态以原Run/Event为准；终态写入失败会另有`execution_terminal_write_failed`。例如未引用导语可定位为`answer_validation/document_paragraph_missing_citation`，公开码仍为PROVIDER_INVALID_RESPONSE；本专项不放宽引用校验。

定向入口：`tests/test_execution_diagnostics.py`、`check_runtime.py tests/test_diagnostics_pg.py`及前端`src/runErrors.spec.ts`。浏览器按既有Playwright命令执行，已安装Edge的设备可显式设置`MINIMEW_TEST_BROWSER_CHANNEL=msedge`；省略时沿用默认测试浏览器。详见[实施记录](chapters/06-完整RAG策略.md#章后专项异常反馈实施记录2026-11-14)。

### 回答格式与材料保留修复（v4）

新Run默认冻结v4：在原次数/期限内允许最多一次私有格式纠正，纠正请求无工具，最终仍走原引用校验与持久终结事务；剔除约定分区标题/引用/空白后正文字符须保持，不能改金额、删事实或新增内容。未知引用或再次非法仍失败。聚合先按原问题/子问题保留代表组，再补余量；最终预算丢失代表来源时明确不足。

`MINIMEW_REPAIRS_ENABLED=false`只让之后新Run冻结v3；`MINIMEW_REASONING_ENABLED=false`仍使之后新Run使用v2。已有v1–v3摘要/行为与已保存结果保持；刷新不会重跑旧失败任务，重新提问才创建新Run。

```powershell
uv run --no-sync pytest -q tests/test_answer_repairs.py tests/test_balanced_selection.py
uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_repair_pg.py
uv run --env-file .env --no-sync python scripts/check_knowledge.py --malformed-first-answer
uv run --env-file .env --no-sync python scripts/check_rag_suite.py --samples 1 --write-repair-observation
```

原[观察](examples/rag/chapter06-observation.json)与[修复观察](examples/rag/chapter06-repair-observation.json)并存；同案例跨章节必要原文覆盖2/3→3/3，调用数未增加。受控模型只验证协议/编排，真实DeepSeek质量尚未收费评测。详见[修复记录](chapters/06-完整RAG策略.md#回答格式与聚合覆盖修复实施2026-11-14)。

### 模型缓存与候选策略（6.3）

知识模式现在同时加载已准备的轻量Embedding和Base重排模型。首次换设备需显式准备固定Base缓存（普通启动不下载）：

```powershell
uv run --env-file .env --no-sync python -m minimew.rag.reranker prepare
uv run --env-file .env --no-sync python -m minimew.rag.reranker status
uv run --env-file .env --no-sync python -m minimew.rag.reranker check
```

仅采用既定Base/CPU FP32/batch1，其他模型、GPU和远端API不自动启用。启用知识功能后缺必要资源会启动失败。新Run在原模型快照冻结RAG策略，Hybrid同范围召回/RRF后可有界补全同节原片段和重排，最终保持原Evidence/答案/引用链；旧v1 Run保留Dense语义。`score_kind`与`stage_scores`区分余弦、RRF和重排原始分数；低分或非空命中不自动证明可回答。超窗不截断，重排失败明确记录trace并保留融合顺序，达到Run期限直接停止。

固定本地对照与完整正式链分别执行：

```powershell
uv run --env-file .env --no-sync python scripts/check_rag.py
uv run --env-file .env --no-sync python scripts/check_knowledge.py
```

前者比较自有未发布候选的Dense/Hybrid/补全/Base、编号与例外材料，后者验证正式发布/原Run/持久引用；都创建自有临时资源并清理，不消费开发任务。回答Provider受控、收费0，不作为真实回答质量证明。`check_rag.py`的每路线3次本机耗时不等于可靠p95。详细源码职责与验证范围见[第六章](chapters/06-完整RAG策略.md)。

## 文档入库基础（工程包 A）

当前已实现 Markdown/TXT 的上传、更新版本、个人文档目录和持久 Index Job。**上传成功返回 pending/candidate，不表示已经分块、向量化或发布**；独立Worker、Milvus核验与原子发布已接入，需另行启动Worker。文档UI已接入主导航，也可通过API/Swagger操作；完整模型选择/重建页面留后续。

先按开头安装依赖、执行 `alembic upgrade head` 并启动新版 API，在 `/docs` 登录/Authorize 后使用：

| 方法与入口 | 当前作用 |
| --- | --- |
| POST `/v1/documents` | 新建独立文档；multipart 包含 `file` 和 `idempotency_key` |
| POST `/v1/documents/{document_id}/versions` | 为已有且属于本用户的文档上传新版 |
| GET `/v1/documents` | 本用户文档列表，`limit`/`offset` 分页 |
| GET `/v1/documents/{document_id}` | 查询稳定身份及 current/pending 指针 |
| GET `/v1/documents/{document_id}/versions/{version_id}` | 查询不可变内容来源、版本状态和冻结构建配置 |
| GET `/v1/index-jobs/{job_id}` | 查询原任务状态、阶段、进度与公开错误 |
| GET `/v1/embedding-profiles` | 查看轻量模型与 M3 的固定 revision/维度/预算元数据，不加载模型 |

上传只接受非空 UTF-8 `.md`/`.txt`，默认上限 5 MiB；服务器生成文件身份，文件名不参与磁盘寻址。新增版本操作以 Document ID 指明更新对象，不根据同名文件自动替换。新的预留返回 202；同一提交键/相同意图重发或复用同文档活跃内容构建返回 200；同键不同意图返回 409；越权/不存在统一 404；格式不合法 422，超限 413；存储/数据库故障返回受控 503。响应均 no-store。

首次上传自动建立一个个人 Knowledge Base。新意图冻结该库的目标构建配置及源格式，保存到不可变版本，Job 绑定该版本；同一提交重试优先返回原版本/Job，不受之后配置变化影响。源格式、解析/分块版本、模型 revision、维度和编码策略参与 fingerprint，不能把 TXT/Markdown 或不同向量空间的构建混用。旧 candidate 被新意图替换时明确 superseded/cancelled；旧 current 保留。失败/被替代版本不重新激活，同内容再次提交产生新版本。

配置默认值（已有 `.env` 可以不添加而直接使用默认值）：

```ini
MINIMEW_DOCUMENT_UPLOAD_DIR=data/documents
MINIMEW_DOCUMENT_MAX_UPLOAD_BYTES=5242880
MINIMEW_EMBEDDING_PROFILE=bge-small-zh-v1.5
```

相对上传目录锚定 MiniMew 项目根目录，API/独立 Worker 必须共享同一目录和数据库，不与上级项目或另一套库共用源目录。默认 `data/` 已被 Git 忽略。`MINIMEW_EMBEDDING_PROFILE` 只决定首次建立个人知识库的目标快照，修改环境变量不会改写现有库/Job 或切换已有索引。当前不提供在线更改该目标的写接口；后续通过“重建并切换”完成。轻量模型已有CPU运行能力和显式准备入口；M3仍只有预设，不会自动下载或切换。首版快照中的分块预算是后续实现/案例验证的起点，不是已测出的最优参数。

源文件在数据库提交前原子落盘；提交失败或结果不明时，可能保留不被目录引用的完整源文件，避免删掉实际上已提交的内容。管理命令默认只列出超过一小时、且数据库确认无对应版本的 `.source` 文件：

```powershell
uv run --env-file .env --no-sync python -m minimew.documents.cleanup
# 核对列表与上传目录所属数据库后，才显式执行删除：
uv run --env-file .env --no-sync python -m minimew.documents.cleanup --apply
```

清理与上传使用同一版本级数据库锁，清理不能越过尚未提交的上传；任何已登记版本的文件都保留，包括失败和被替代版本。数据库不可用时停止清理。它是本包完整孤立源文件的管理入口，不代替下文C的已登记向量生命周期清理。

定向检查：

```powershell
uv run --no-sync pytest -q tests/test_documents.py tests/test_migrations.py
uv run --env-file .env --no-sync python scripts/check_documents.py
```

后者使用自有随机临时 PG 库和测试文件，覆盖真实 API、并发、提交失败、旧数据升级以及既有 Thread/Run 回归，结束时清理临时库，不向开发库写测试文档，不调用真实模型/Redis/Milvus。可在命令后传具体 pytest 文件/筛选参数。实际本轮分组结果与限制见 [第四章工程包 A 记录](chapters/04-文档入库与发布.md#工程包-a-实施记录2026-11-09)。

## 本地轻量 Embedding（B.3）

这是独立的模型准备/编码基础，使用 `BAAI/bge-small-zh-v1.5` 的固定 revision，输出归一化 512 维向量。本步只实现 CPU；M3 仍只有预设，尚未准备。本节CLI仍只做独立模型准备/检查；正式任务链路见下文C.1–C.3。

在 MiniMew 目录执行：

```powershell
# 可选依赖：现有普通后端命令不要求安装模型库。
uv sync --dev --extra embedding --locked
# 显式联网，只准备固定版本所需的配置、Tokenizer 和 safetensors。
uv run --no-sync python -m minimew.embeddings.cli prepare
# 只核对本地文件，不代表真实编码验证通过。
uv run --no-sync python -m minimew.embeddings.cli status
# 只读本地模型；真实 CPU 编码和固定小样例的相似排序检查。
uv run --no-sync python -m minimew.embeddings.cli check
```

默认不需要 `.env`、数据库、Redis 或 DeepSeek 密钥。若将自定义缓存参数放入本机 `.env`，在上述 `uv run` 中显式加 `--env-file .env`；程序本身不隐式读取 dotenv。支持的运行参数：

```ini
MINIMEW_EMBEDDING_CACHE_DIR=data/models
MINIMEW_EMBEDDING_BATCH_SIZE=4
```

相对缓存路径锚定 MiniMew 根目录，与当前终端目录无关。模型文件位于该目录的 Hugging Face 固定 revision 快照内，不进入 Git；新设备需要重新 `prepare`，或者另外传输完整缓存。`uv sync --dev --locked` 不启用 embedding extra，会移除其专用依赖；要运行模型需使用带 `--extra embedding` 的同步命令。当前 Windows/Linux torch 来源限定为官方 CPU index，没有 CUDA 运行承诺，也不要求安装系统 CUDA Toolkit。

`prepare` 不加载模型，只确认所需文件齐全；`status` 返回 `files_present` 和 `encoding_verified: false`；`check` 才加载本地权重并编码三段正文和两个问题，返回维度、排序、相似分数及耗时，预期排序不符则退出非零。这只是固定 smoke，不是正式知识库检索、模型质量评测或已经发布的文档索引。Windows 未启用符号链接时 Hub 可用普通文件缓存，不需要为此修改系统权限。

正常加载只用本地固定快照和 safetensors，禁止远程代码，不会在缺文件时改用其他模型或下载补齐。公开错误为稳定 code：`EMBEDDING_NOT_PREPARED`（先准备）、`EMBEDDING_DEPENDENCIES_UNAVAILABLE`（先安装 extra）、`EMBEDDING_UNSUPPORTED_PROFILE`、`EMBEDDING_MODEL_MISMATCH`、`EMBEDDING_INPUT_TOO_LONG`、`EMBEDDING_INVALID_INPUT/OUTPUT`、`EMBEDDING_LOAD_FAILED/ENCODING_FAILED/PREPARATION_FAILED/CACHE_UNAVAILABLE`。缺模型、编码失败不返回空检索结果。

代码职责：

| 文件 | 职责 |
| --- | --- |
| [embeddings/config.py](src/minimew/embeddings/config.py) | 独立的缓存/批量运行配置，不要求 API 认证和数据库凭据 |
| [embeddings/artifacts.py](src/minimew/embeddings/artifacts.py) | 唯一显式下载入口、固定模型/revision 白名单及完整本地文件检查 |
| [embeddings/adapter.py](src/minimew/embeddings/adapter.py) | 显式加载 CPU 模型；正文/问题编码、特殊 Token/前缀长度、批量和向量校验；同步接口 |
| [embeddings/cli.py](src/minimew/embeddings/cli.py) | prepare/status/check，复用同一 Adapter，不调用聊天 Provider |
| [test_embeddings.py](tests/test_embeddings.py) | 固定假 Encoder/Tokenizer 的离线行为验证，不下载/加载真实权重 |

正文的标题拼接由后续 chunk 构造方负责，Adapter 不猜测标题和来源；问题按冻结预设增加查询 instruction，两者不能混用。每批先按真实 Tokenizer 检查完整输入，包含特殊 Token；超限明确失败，不静默截断。每行必须为 512 个有限数值且范数接近 1，失败整次调用不返回部分成功结果。对象加载一次后可重复编码，不在每个 chunk 上重新加载；未来异步调用须放进受控线程，不能直接阻塞 API 事件循环。

离线验证命令：

```powershell
uv run --no-sync pytest -q tests/test_embeddings.py
```

当前实现与真实检查记录见 [第四章](chapters/04-文档入库与发布.md)；实际进度和授权仍以交接为准。

## 文档解析与分块预览（B.1/B.2）

已接入真实 Markdown/TXT → 原文定位 → 标题上下文分块 → 现有 CPU 编码 Adapter。正式Worker现已复用该模块处理上传任务；本地预览入口本身仍不会登记文档/版本、改变current或写数据库。

使用前按上一节准备轻量模型和 embedding extra。新设备仍需先按锁安装依赖；markdown-it-py 已直接声明，不依赖模型库的传递安装。

```powershell
# 显示每块的原文、编码文本、章节/来源位置、稳定 ID、hash 和 Token 数。
uv run --no-sync python -m minimew.documents.preview examples/rag/expense-policy.md
# 再用已有 Adapter 对这些片段做真实 CPU 编码，输出向量数量和维度。
uv run --no-sync python -m minimew.documents.preview examples/rag/expense-policy.md --encode
# 固定样例 + BOM/CRLF 长段落，检查覆盖、预算、稳定身份和真实编码。
uv run --no-sync python scripts/check_chunking.py
```

也可将示例路径换成自己的 UTF-8 `.md`/`.txt` 文件（最多 5MiB）；检查入口只读源文件。运行参数来自上一节的 `MINIMEW_EMBEDDING_CACHE_DIR` 等环境变量，自定义值保存在 `.env` 时需显式加 `--env-file .env`。预览身份是确定性的本地样例 UUID，并标明 `mode=local_preview`、`registered_version=false`，不能当作数据库中的正式版本或权限证明。

原文定位单位是 UTF-8 原始解码字符串的 **Unicode code point**：`start/end` 为 0-based `[start,end)`，行号为 1-based 且包含末行。原文不做 BOM 去除、换行归一化或 Token 解码重建；BOM 不作为正文编码，Markdown 解析器的行范围映射回原始 CRLF/CR/LF。字符位置不是 UTF-8 字节偏移，也不是浏览器 UTF-16 下标；后续跨端引用应按明确单位换算或使用行号。`source_text` 恒等于该原文位置的切片，`embedding_text` 单独添加所属标题路径，不能拿后者的位置回指源文。

Markdown 使用 CommonMark 顶层结构并启用普通管道表格，不渲染 HTML、执行代码或读取链接；TXT 按空白行分段，不猜标题。标题仅作为上下文，段落/列表优先按结构组织，长段落先沿句子/换行，再沿 Token 原文位置拆；最终输入计入标题和特殊 Token，满足 384 预算。同节 overlap 最多 48 个正文 Token，必要时减少以保证前进；不跨章节、代码块或表格机械重叠。代码及普通表格保持完整，嵌套代码/表格所在外层结构也按完整块处理。

代码/表格超预算返回 `DOCUMENT_BLOCK_TOO_LARGE`，标题自身超预算返回 `DOCUMENT_TITLE_TOO_LONG`，仅标题无正文返回 `DOCUMENT_NO_CONTENT`；不会丢弃超长部分、移除标题后继续或假称分块成功。来源缺失/损坏返回 `DOCUMENT_SOURCE_UNAVAILABLE`，原始内容 hash 不匹配返回 `DOCUMENT_SOURCE_HASH_MISMATCH`。配置/Tokenizer 身份不匹配返回 `DOCUMENT_BUILD_CONFIG_MISMATCH`。这些失败不等于“知识库没有知识”。

chunk 身份覆盖用户/知识库/文档/版本、源 hash、构建 fingerprint、序号、位置与正文/编码文本 hash。相同身份/输入/配置重复构建结果稳定；不同版本、归属或配置不混用。章节记录保存父节关系及原文范围；**当前不会自动把父节适用条件或远处例外拼进每个子块**，后续检索仍需按预算补足上下文。

| 源码 | 职责 |
| --- | --- |
| [documents/parsing.py](src/minimew/documents/parsing.py) | 保留原文，识别块/章节和父节，映射准确位置 |
| [documents/chunking.py](src/minimew/documents/chunking.py) | 版本/构建身份、结构优先拆分、同节重叠、稳定产物；`build_version_chunks` 复用 SourceStore 的完整性读取 |
| [embeddings/tokenizer.py](src/minimew/embeddings/tokenizer.py) | 固定本地 Tokenizer 的完整计数与字符对齐，不加载权重或下载模型 |
| [documents/preview.py](src/minimew/documents/preview.py) | 只读本地样例预览，复用同一构建模块及编码 Adapter |
| [check_chunking.py](scripts/check_chunking.py)、[测试](tests/test_document_chunking.py) | 显式真实 CPU 检查，与固定假 Tokenizer 的离线边界测试分别记录 |

```powershell
uv run --no-sync pytest -q tests/test_document_chunking.py
```

本轮实际结果见 [第四章实施记录](chapters/04-文档入库与发布.md)，进度与下一工作包授权见交接。

## 候选向量索引与检索观察（B.4/B.5）

已有真实 Milvus 候选写入、精确 manifest 核验和受控 top-k 检查。本节候选CLI独立于正式Index Job，检查结果仍明确 `published=false`；正式上传消费/发布见下文C.1–C.3。正式问答入口尚未开放候选查询。

在 MiniMew 目录执行：

```powershell
uv sync --dev --extra embedding --extra vector --locked
# 当前设备镜像已缓存，明确不拉新镜像，只启动本项目这个服务。
docker compose --profile vector up -d --no-deps --pull never milvus
docker compose --profile vector ps milvus
# 如前面的轻量模型尚未准备，先执行 Embedding 章节的 prepare。
uv run --env-file .env --no-sync python -m minimew.vector_store.cli check
```

新设备没有缓存时，可以先显式执行 `docker compose --profile vector pull milvus` 获取锁定镜像，再启动。本机采用 Milvus 3.0.0 的固定镜像摘要和 PyMilvus 3.0.0，未安装 SDK 的模型/Lite extra。后续同步环境时必须同时保留 `--extra embedding --extra vector`；省略其中一个会移除其专用依赖。普通 API 不依赖 SDK 连接，模块导入不启动服务或加载模型。

Milvus 为本项目独立容器 `minimew-milvus-1`、独立卷 `minimew_milvus_data`，仅暴露 `127.0.0.1:59530`。使用内嵌 etcd 和本地存储，不启动旧 Milvus/MinIO/etcd，也不挂载旧项目数据；etcd 和健康端口不映射宿主机。Compose 将本服务限制为 2 CPU/3GiB，供当前小样例验收；这不是生产容量配置。当前开发容器无认证且仅本机访问；有需要时单独停止本服务：

```powershell
docker compose --profile vector stop milvus
```

运行配置：

```ini
MINIMEW_VECTOR_PORT=59530
MINIMEW_VECTOR_TIMEOUT_SECONDS=15
MINIMEW_VECTOR_BATCH_SIZE=4
MINIMEW_VECTOR_TOKEN=
```

CLI 本身不依赖 DB/Redis/回答模型密钥；`--env-file .env` 用于显式读取本机运行参数，使用默认值时可省略。令牌是 Secret，只用于客户端连接，不进入 manifest/正文/输出。不要将已停止旧容器的存在当作新服务连接成功，查看本项目健康状态后再执行 check。

检查入口会用已有报销制度构建7块，先写2块，再以原定7块清单补齐/核验，重复同意图操作，并检查不同尝试/用户的隔离。它查询餐费、住宿及一个不相关问题，输出 manifest、原文位置和 top-k 分数。最后只清理本次自有候选记录，保留集合/schema和其他范围，`cleanup=complete` 后示例记录不再保留；这不影响正常 Adapter 的持久写入能力。检查不会登记虚假 Document Version、改 Job/current 或访问旧业务集合。报告可保存到本机忽略的 `.artifacts/`，不能当作已发布目录。

每行保存512维向量、原文/编码文本和来源元数据。Collection 名称由固定编码空间派生，字段/维度/长度、动态字段、描述和 FLAT/COSINE 必须符合正式定义；不符明确失败，不自动删除重建。稳定 chunk ID 与物理主键分开：物理主键额外包含候选尝试，旧尝试的迟到写入/清理不能碰新尝试。manifest 在写入前冻结每行预期身份与正文/向量内容 hash，精确读回用 Strong 一致性分页检查，不能用 top-k 或仅条数代替；重试会先核对已有记录，只补缺失，不覆盖冲突内容。

RPC 使用明确的 gRPC deadline并禁用 transport retry；PyMilvus 每调用显式 `retry_times=0`、`retry_on_rate_limit=false`、`timeout=None`，deadline由channel service config提供。原因是固定SDK的numeric timeout会优先用时间作为重试预算，单独传 retry_times=0 不足以关闭它；单次失败只抛稳定错误，不叠加Adapter重试。加载状态的正常轮询有界。查询必须使用核验回执及兼容编码配置，过滤范围由结构化身份生成，不接受任意集合/filter输入。

主要错误：`VECTOR_COLLECTION_MISMATCH`、`VECTOR_BUILD_MISMATCH`、`VECTOR_MANIFEST_MISMATCH`、`VECTOR_INVALID_VECTOR/QUERY/RESULT`、`VECTOR_PAYLOAD_TOO_LARGE`、`VECTOR_PARTIAL_WRITE`、`VECTOR_UNAVAILABLE`、`VECTOR_LOAD_TIMEOUT`、`VECTOR_DELETE_INCOMPLETE`。网络/存储失败明确报错；只有正常搜索为空才返回空列表。top-k通常仍会返回不相关片段，返回结果不等于足够回答，之后章节再接证据判断/拒答，不把相似分数当概率。

| 源码 | 职责 |
| --- | --- |
| [manifest.py](src/minimew/vector_store/manifest.py) | 写前预期清单、版本/尝试范围、物理主键与 float32/正文内容校验 |
| [adapter.py](src/minimew/vector_store/adapter.py) | stage/verify/search/delete_candidate 唯一业务接口；缺失补齐、冲突拒绝、回执和范围校验 |
| [milvus.py](src/minimew/vector_store/milvus.py) | PyMilvus连接、严格集合初始化/校验、Strong读回、FLAT/COSINE和RPC策略 |
| [config.py](src/minimew/vector_store/config.py) | 独立本机连接、Secret、RPC预算和批量运行参数 |
| [cli.py](src/minimew/vector_store/cli.py) | 复用 preview 的结构化构建、轻量编码及同一候选Adapter，检查后精确清理 |
| [test_vector_store.py](tests/test_vector_store.py) | 离线假backend与SDK假RPC，验证部分提交/丢响应/冲突/隔离/故障和零重试 |

```powershell
uv run --no-sync pytest -q tests/test_vector_store.py
```

本包不拥有正式发布或租约领取；C 将把每次执行资格、候选产物登记和 current 切换接入持久事务。`VerifiedCandidate` 只是核验回执，不是用户授权凭据或 published 状态。实际结果和下一步范围见第四章与交接。

## 持久索引Worker与发布（C.1–C.3）

已有正式上传→持久Job→独立Worker→解析/分块/CPU编码→Milvus核验→原子发布链路。
上传返回的202仍表示已接收；只有Job查询为`succeeded`、Document的current指向该版本才算发布。
Worker不随API启动，不创建Celery/RabbitMQ队列，也不在HTTP里运行模型。

在MiniMew根目录、已准备轻量模型及独立Milvus后，另开终端运行：

```powershell
uv sync --dev --extra embedding --extra vector --locked
uv run --env-file .env --no-sync alembic upgrade head
docker compose --profile vector up -d --no-deps --pull never milvus
# 先核对目标DB、源目录及待处理任务；本命令会真实消费该库的持久文档任务。
uv run --env-file .env --no-sync python -m minimew.documents.worker
# 调试时最多处理一个构建及一个到期清理任务后退出：
uv run --env-file .env --no-sync python -m minimew.documents.worker --once
```

没有缓存镜像的新设备先显式pull，缺轻量权重先用Embedding章节的prepare；Worker本身只读本地缓存，缺失会在领取前失败，不隐式下载。API与Worker必须连接相同PG、源目录和Milvus数据。不要把本项目Worker指向SuperMew的数据。Ctrl+C停止领取，已在途阶段返回后完成当前工作；强制杀进程后由租约过期恢复，不宣称同步模型能被硬取消。

默认每进程一个在途构建，另有独立异步心跳。`MINIMEW_INDEX_POLL_SECONDS=1`、`MINIMEW_INDEX_LEASE_SECONDS=30`、`MINIMEW_INDEX_MAX_ATTEMPTS=5`、`MINIMEW_INDEX_JOB_TIMEOUT_SECONDS=600`；已有.env可沿用默认值，不必重写Secret。Embedding与向量写入分别使用`MINIMEW_EMBEDDING_BATCH_SIZE`和`MINIMEW_VECTOR_BATCH_SIZE`。新构建尝试采用独立namespace；不是把旧owner身份重新交给新Worker。

持久状态由PG保存，模型/向量SDK在受控线程执行。每次向量RPC与该attempt清理共用advisory锁，并重新核对资格，防止删除后迟到写入；只在单个有期限的RPC中占用专用锁事务，不持Document/Job业务行锁，不把模型计算包入数据库事务。跨存储步骤不伪装成整体可回滚事务。

- `running`构建尚未确认staged就退出：重新领取后退休旧attempt，新namespace重建；旧范围由持久清理处理。
- `staged`已经持久保存原清单并核验：重新领取、读回原清单、重验原向量，再发布；不重新分块或编码。缺清单或核验不符明确失败，不倒造预期。
- 暂时故障按DB时钟设置下一次重试；确定性失败为failed，预算耗尽为dead_letter。失去资格只停止本地交付；不写终态或在finally乱删。
- 发布事务一起处理current、版本/Job终态与清理登记；提交回包丢失再查原持久资格，成功的Job不会被改回失败。
- 新版发布后旧向量保留1小时；内部读取scope有效55分钟并检查到期，供已经取得旧scope的请求完成。新scope只选择用户当前已发布版本，不提供候选HTTP入口。
- 清理只删除已退休的精确attempt向量并强一致读回为空；PG清单/分块账本及已登记source继续保留用于审计/定位。cleanup complete不表示删除整个Document或源文件。

清理dead-letter须由操作员核对对应artifact后显式重排，不恢复文档发布资格：

```powershell
uv run --env-file .env --no-sync python -m minimew.documents.worker --requeue-cleanup "ARTIFACT_UUID"
```

`index_worker_heartbeats`记录Worker身份、支持的fingerprint、DB时间和running/stopped。现有`/health/ready`仍仅证明PG连接；模型、Milvus和Worker可用性不能由该接口推定。当前支持轻量CPU；M3未提供运行能力或切换UI。

| 源码 | 职责 |
| --- | --- |
| [catalog.py](src/minimew/documents/catalog.py) | 上传预留/幂等/归属；新上传覆盖候选时同事务退休旧产物 |
| [jobs.py](src/minimew/documents/jobs.py) | claim/lease/fence、进度、清单登记、staged、发布CAS与重试/终态的短事务 |
| [artifacts.py](src/minimew/documents/artifacts.py) | 冻结清单与分块的序列化/完整性校验，退休与清理入账 |
| [worker.py](src/minimew/documents/worker.py) | 独立进程、CPU/SDK线程、心跳、恢复与有界调度；复用既有B构建/Vector模块 |
| [index_cleanup.py](src/minimew/documents/index_cleanup.py) | cleanup独立租约/fence、重放、失败/重排；不删除source |
| [retrieval.py](src/minimew/documents/retrieval.py) | 同一Catalog快照下按用户取得published scope，复用Vector查询；还不是Agent Tool |
| [0012迁移](migrations/versions/0012_index_artifacts_publication_worker.py) | 三张新表、重试字段、staged重领和不可变产物guard；保留0011数据 |

定向重现（需要本机PG；真实进程项另外需要已准备模型和Milvus）：

```powershell
uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_indexing_pg.py tests/test_indexing_migration.py
uv run --no-sync pytest -q tests/test_documents.py tests/test_vector_store.py
uv run --env-file .env --no-sync python scripts/check_indexing.py
```

PG故障测试使用隔离临时库和受控编码/向量backend，真实进程检查走正式API和独立Worker入口：7块发布、餐费检索定位原版本、错误新版不替换旧版；结束精确清理自有scope/临时库，不删除整个集合。报告为忽略的`.artifacts/indexing-process-check.json`。C.1–C.3已将开发库升级0012并核对旧行hash；C.4/C.5未写开发数据，未自动常驻消费。文档UI/浏览器已在下节交付，知识库回答留第五章；详见[本包记录](chapters/04-文档入库与发布.md#c1c3-后端闭环实施记录2026-11-11)。

## 文档工作区（C.4/C.5）

启动/重启新版API以加载`latest_job`文档查询投影，按上节另起索引Worker，再启动前端。登录后点击主导航“文档”：

1. “上传新文档”创建独立Document；只接收非空UTF-8 Markdown/TXT，最大5 MiB。
2. 从目录选择文档，“上传新版”使用同一Document身份；文件名相同不会自动匹配为更新。
3. “当前可用版本”显示已发布知识，“最近构建”显示最近一次Job；新版失败后两者同时保留，刷新仍能恢复。
4. 构建中定时读取原Job与文档指针，显示阶段/片段进度；staged是待发布。进度查询失败显示上次结果，可手动重试；上传回包不明确时重试同一意图使用原幂等键。

页面支持分页、账号隔离、离页停止查询和手机布局；Access Token仍只在内存。冻结模型信息来自该版本，当前支持轻量CPU，M3明确尚未支持；静态能力说明不代表Worker在线。知识库回答与完整模型重建切换属于后续阶段。

代码入口是[页面](frontend/src/views/DocumentsView.vue)、[业务store](frontend/src/stores/documents.ts)、[响应解析](frontend/src/documents.ts)、[API查询投影](src/minimew/documents/catalog.py)。版本/Job事实仍在PG，store不是第二套持久目录。

本包定向重现：

```powershell
uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_document_projection_pg.py tests/test_documents_pg.py
uv run --no-sync pytest -q tests/test_documents.py
uv run --env-file .env --no-sync python scripts/check_documents_frontend.py
```

前端目录中：

```powershell
npm run test -- src/documents.spec.ts src/stores/documents.spec.ts
npm run typecheck
npm run build
```

真实浏览器检查使用独立临时PG、源目录、API与实际CPU/Milvus Worker进程，不连接开发库任务；只有明确启用该脚本才执行真实文档E2E。脚本复用正式入口，测试停止适配在模型预热后接入，避免本机Windows NumPy导入时与阻塞stdin线程冲突。结束停止自有进程、精确删除测试scope并回收临时库。运行报告在`.artifacts/document-browser-*/report.json`，截图在`.impeccable/review/`，不属于业务数据或生产容量证明。

第四章完整[总结](chapters/04-文档入库与发布.md#结章文字总结2026-11-11)与[代码职责框架](chapters/04-文档入库与发布.md#本章结束时的代码框架2026-11-11)已保存；实际106个本包用例与前包历史结果分开记录。第五章再把已发布检索接入Agent/Tool/Evidence和引用回答。

## 对话接口

接口统一位于 `/v1/threads`，均要求 `Authorization: Bearer <access_token>`。可重启后端后在 `/docs` 登录、复制 Access Token 并通过 Authorize 输入，随后试用：

| 请求 | 输入 | 成功结果 |
| --- | --- | --- |
| `POST /v1/threads` | JSON `{}` | 201；返回 `id`、`title`、`created_at` |
| `GET /v1/threads?limit=20&offset=0` | limit 为 1–100，offset 非负 | 200；返回 `items`、`next_offset`，没有下一页时为 null |
| `GET /v1/threads/{thread_id}` | 创建接口返回的 UUID | 200；返回该对话 |

创建时服务端生成 ID，从已验证的身份获取归属，并将默认标题“新对话”写入数据库；不接受请求体指定 user_id、id 或 title，额外字段返回 422。提交成功才返回 201。每次创建请求都会创建独立 Thread；本步没有创建幂等键，后续 Run 提交幂等属于另一项功能。

列表只查询当前用户，按 created_at、id 均倒序，next_offset 可用于下一页。当前使用简单 offset 分页；翻页期间若有新对话插入，可能出现重复或位移，不提供跨请求快照保证。单个查询同时限制 ID 和用户归属，不存在或属于他人都返回相同 404；非法 UUID 返回 422，缺失/无效身份返回 401。成功响应带 `Cache-Control: no-store`。

标题规则已实现：首次预留 Run、保存用户消息时，在同一事务内更新标题；后续消息不覆盖。标题去除首尾空白、连续空白合并为空格，超过 30 个字符截断并加省略号；消息原文不变。模型失败仍保留已提交标题。没有改名或删除接口。

Thread 的归属隔离、分页、跨应用读取和失败回滚纳入 `scripts/check_models.py` 的临时 PostgreSQL 验证；默认 pytest 的离线测试验证未认证访问在数据库操作前被拒绝。

## 提交提问与查询（2.1）

以下接口需要 Bearer Access Token，创建 Run 前必须已有属于当前用户的 Thread。可在 `/docs` 或聊天页面试用；前端输入框已接入该正式入口。

| 请求 | 输入与结果 |
| --- | --- |
| `POST /v1/threads/{thread_id}/runs` | `idempotency_key` 与 `content`；首次 201，完全相同的重发 200，键相同而正文不同 409 |
| `GET /v1/threads/{thread_id}/runs/{run_id}` | 返回 Run/Thread ID、状态、消息 ID、时间、取消请求时间与受控错误码 |
| `POST /v1/threads/{thread_id}/runs/{run_id}/cancel` | 对原 Run 请求取消；pending/已有终态返回 200，running 保存请求返回 202 |
| `GET /v1/threads/{thread_id}/messages?limit=50` | 最新一页，items 内按 sequence 升序；previous_cursor 非 null 时可用 `before=该值` 获取更早页 |

提交示例：

```json
{"idempotency_key":"question-001","content":"解释一下 RAG"}
```

键长度 1–128，仅接受字母、数字、下划线、点、冒号和短横线；按所属 Thread 隔离。正文长度 1–20000，拒绝空白和 NUL，保留原文；幂等按原文精确比较，因此不同空白也属于不同内容。同一意图因响应丢失重发时复用原键；新的提问用新键。不同键即使正文相同也创建新 Run。本步没有重新生成或恢复失败任务接口。

事务顺序：校验归属并锁定 Thread → 查幂等记录 → 读取当前模型并冻结快照（2.2 新增）→ 创建带快照的 pending Run → 创建 completed 用户 Message 与空正文的 pending assistant Message → 更新 last_sequence 和首条消息标题 → 提交。completed 在这里表示用户消息已保存，不表示模型执行完成。run_id 与 role 的唯一约束限制每个 Run 最多一条用户/assistant 消息；应用事务保证正式创建一定写齐一对。复合外键防止 Run 的用户与 Thread 不符、Message 与 Run 的 Thread 不符；唯一序号保证顺序不重复。数据库不直接阻止管理员绕过接口只写孤立 Run，正常业务必须通过原子预留入口。

查询始终校验当前用户归属，越权、不存在或错误的 Thread/Run 组合返回统一 404。历史每页 1–100 条，before 是正整数 sequence；分页可能在一对消息之间分界。服务故障返回错误，不伪装为空历史。没有自动创建 Thread 或进程内记录替代数据库。

API 先预留带快照的 pending Run；2.7 的后台调度器自动领取并使用真实 DeepSeek 生成答案，分页/SSE 读取同一份持久 Journal。fake 只在隔离验收中注入；2.8 已接入按 Run 取消。2.1 历史无快照 Run 的保留与禁止执行策略见下文。

新增 `conversations.py` 持有事务规则，`api/runs.py` 负责 HTTP 映射；历史仍由 Thread 的唯一 HTTP 入口提供。OpenAPI/TS 已从正式路由同步生成。真实 PostgreSQL 的并发、幂等、回滚、权限和迁移检查纳入 `scripts/check_models.py`；迁移 0005 禁止降级丢弃消息历史。

## 模型配置与快照（2.2）

`model_profiles` 保存无 Secret 的模型配置和正整数版本；`model_assignments` 保存当前 answer 角色的选择。本阶段只有一个模型角色。迁移只建立空选择，不替用户选择模型；当前配置以 PostgreSQL 为准。配置管理使用本地命令，后续再接管理网页。

用户选择 DeepSeek V4 Flash、关闭深度思考。仓库中的 `model-profile.deepseek-v4-flash.json` 为无密钥配置：

```json
{
  "provider": "openai_compatible",
  "model_name": "deepseek-v4-flash",
  "base_url": "https://api.deepseek.com",
  "temperature": 0.2,
  "max_output_tokens": 4096,
  "timeout_seconds": 60.0,
  "thinking": {"type": "disabled"}
}
```

2026-10-30 核对 [DeepSeek 官方入口说明](https://api-docs.deepseek.com/en/)：旧 ID `deepseek-v4-flash` 仍接受，但官方实际由 DeepSeek-V4.1-Flash-1017 提供服务；本项目保留用户指定 ID，不宣称锁定旧模型权重。关闭思考使用 `thinking.type=disabled`，参照 [官方思考模式说明](https://api-docs.deepseek.com/guides/thinking_mode/)。配置校验要求显式填写该值，拒绝 enabled、未知字段、API Key/Header，以及含用户名、密码、query 或 fragment 的服务地址。2.7 Adapter 在每次实际请求中传入此参数。

在 MiniMew 目录完成迁移后，运行：

```powershell
uv run --env-file .env --no-sync alembic upgrade head
uv run --env-file .env --no-sync python -m minimew.model_cli set --name deepseek-v4-flash --file model-profile.deepseek-v4-flash.json
uv run --env-file .env --no-sync python -m minimew.model_cli show
# 替换为已有 Run 的 UUID，查看这次执行自己的快照。
uv run --env-file .env --no-sync python -m minimew.model_cli show-run <run_id>
```

这些命令使用本地数据库管理权限，输出无 Secret 配置和摘要。API Key 由真实 Provider 从服务端配置读取；不要写进 JSON、命令参数或数据库。命令不创建测试 Run，不触发模型请求。修改 JSON 后再次 set，同名 Profile 内容有变化时 version 加一，内容相同保持原版本；使用其他 name 会创建或选择该 Profile。选择与更新在同一事务内提交。

创建新 Run 时，在原有预留事务中保存 `model_snapshot_json` 与 `model_catalog_hash`。快照包含 schema_version、answer 的 Profile ID/版本和完整调用配置；摘要是对确定性 JSON 的 SHA-256。配置更新与冻结按相同顺序锁定选择和 Profile，防止读取到混合版本；不同 Thread 可并发读取。Pydantic 的嵌套配置不可修改，数据库触发器进一步拒绝改写 Run 的快照与摘要，参照 [PostgreSQL 触发器说明](https://www.postgresql.org/docs/current/plpgsql-trigger.html)。摘要用于验证已保存配置的一致性，不能保证第三方同名模型永不更新或生成答案逐字相同。

同键同正文重发先返回原 Run，不读取当前配置；因此更新配置或当前配置不可用，不改变原请求结果。新键创建时若当前配置缺失或非法，返回 `503 {"detail":{"code":"MODEL_CONFIGURATION_UNAVAILABLE"}}`，不留下 Run、消息、序号或标题的部分变更。这个错误不表示检索无知识，也不代表数据库 readiness 一定失败。公开 Run 响应不包含私有模型地址或完整快照。

迁移 0006 原样保留 2.1 历史 Run、消息和状态，历史无快照字段保持 NULL，不补填当前配置。`snapshot_for_run` 明确返回 `MODEL_SNAPSHOT_MISSING`，执行器通过此入口读取；`show-run` 对这些旧 Run 也以该错误退出。2.3 若显式驱动旧任务执行，会记录该失败码并保持原消息归属，绝不调用模型或补猜配置。新插入 Run 缺快照、改写旧快照或给历史 Run 补猜快照，都会被数据库拒绝。旧记录仍可按原 HTTP 查询和幂等规则读取；要发起新的提问意图应创建新 Run。0006 禁止 downgrade。

## 持久执行与事件（2.3–2.6）

正式预留链使用可信内部执行接口 `RunExecutor.execute(user_id, thread_id, run_id)`；2.7 后默认由应用的调度器调用同一接口，不另建聊天 route。账户前端暂时只支持身份和 Thread 管理，可在 `/docs` 提交问题，再通过 Run/消息历史/事件接口查看答案。

职责按业务分为：

| 文件 | 做什么 |
| --- | --- |
| [execution/provider.py](src/minimew/execution/provider.py) | 定义唯一流式 Provider 接口、正文分片/明确完成结果、不可变请求和稳定调用错误；不读写数据库 |
| [execution/context.py](src/minimew/execution/context.py) | 将完整历史问答和本次问题整理为有界 messages |
| [execution/repository.py](src/minimew/execution/repository.py) | 用独立 Session 管理领取、输入读取、分片追加、结果提交与保留失败前缀的过期终结 |
| [execution/journal.py](src/minimew/execution/journal.py) | 校验契约、分配单 Run 事件序号并追加持久事件 |
| [execution/service.py](src/minimew/execution/service.py) | 串联领取、输入、Provider 调用和结果提交；独占模型有界重试与绝对预算 |
| [execution/deepseek.py](src/minimew/execution/deepseek.py) | LangChain/DeepSeek SDK 适配、关闭思考、流/错误转换和每次调用的客户端清理 |
| [execution/dispatcher.py](src/minimew/execution/dispatcher.py) | 随应用扫描可执行 pending、限制本实例并发、定期终结过期 running 与停机清理 |
| [execution/queries.py](src/minimew/execution/queries.py) | 校验用户/Thread/Run 归属，按游标分页读取已提交事件 |
| [execution/streaming.py](src/minimew/execution/streaming.py) | 每次订阅独立读取并传输事件，管理心跳、到期、发送背压和断连清理；不执行模型或改写 Run |
| [tests/execution_helpers.py](tests/execution_helpers.py) | 只在隔离验收中使用的可控 fake Provider |

一次执行的实际顺序：短事务按 Thread → Run 顺序加锁并条件领取，提交 running/owner token/租约和 run.started → 短事务读取冻结快照与历史 → 在数据库事务之外消费 Provider 流，每批正文通过独立短事务追加 message.delta → 结果事务填入原 assistant Message，并追加 message.completed 和 run.completed/run.failed/run.cancelled，最后条件更新 Run 终态并提交。结果事务任意一步失败，本次最终正文、完成状态和结束事件一起回滚；已提交的领取状态与此前的分片保留，不向调用方报告成功。

同 Thread 的较早 pending/running 会阻止后续 Run 领取，不同 Thread 可并行。历史仅选择本次提问之前的成功完整问答；失败或取消轮次保留在业务历史中，不传给后续模型。最多取最近 20 轮、总正文 60000 字符，超限按完整问答裁剪最旧部分，始终保留本次问题；这些是字符/条数预算，不是精确 token 计数。未来消息和当前空 assistant 占位不会作为输入。

默认租约 330 秒，Provider 的绝对调用截止取快照 timeout 与剩余租约的较小值；普通异步 Provider 用 [asyncio timeout](https://docs.python.org/3.12/library/asyncio-task.html#timeouts) 协作取消。吞掉取消后迟到返回的结果也会被截止时间和数据库所有权检查拒绝。租约不证明外部请求已停止，不提供外部调用严格一次保证。当前没有自动续租或重跑；`recover_expired()` 把过期任务记录为 `EXECUTION_EXPIRED`，2.7 调度器定期驱动该入口。

Provider 超时、异常、无效响应分别记录稳定 code，不保存原始异常、地址或密钥。结果库写入故障直接传播，不伪装成 Provider 错误或无知识。执行器协程中断时会尝试记录 `EXECUTOR_INTERRUPTED` 并重新抛出取消；这不等于面向用户的 Run 取消接口，进程崩溃后的清理由过期终结处理。

数据库除了检查状态与同 Thread 唯一 running，还使用延迟到事务末尾的约束触发器，核对 Run、assistant Message、完成事件与事件序号一致。直接只提交 succeeded 而不保存回答和事件会被拒绝。已完成消息、终态 Run 和 Journal 不可改写；Journal 与消息历史不能被直接删除。历史快照 pending 保留原状、不补造 run.created，首次执行可以从 run.started 的 sequence=1 开始。

新 Run 在预留事务内写入 run.created。没有正文分片的成功 Run，最小日志依次是 run.created、run.started、message.completed、run.completed；流式执行在 started 与结束事件之间追加 message.delta，失败时最后一个为 run.failed。Run 的 last_event_sequence 与 Thread 的 Message sequence 是两个不同序列。公开 Run 查询支持 pending/running/succeeded/failed/cancelled、finished_at、cancel_requested_at 与 error_code，不输出 owner token、租约或私有快照；失败的 assistant Message 状态为 failed。同键重发已完成 Run 只返回当前持久结果，不再次调用 Provider。

事件真源为本目录 [run_event_v1.json](contracts/run_event_v1.json)，生成 Python 的严格 payload/状态/错误类型和 TypeScript 联合类型。需要新增或调整事件时运行：

```powershell
uv run --no-sync python scripts/generate_event_contract.py
uv run --no-sync python scripts/generate_event_contract.py --check
uv run --no-sync python scripts/export_frontend_contract.py
# 在 frontend 目录运行 npm run contracts，同步 HTTP 消费者。
```

不要编辑生成文件。生成器使用已有 Ruff 开发依赖，固定 LF 换行；遇到不支持的 schema 结构明确拒绝。分页与 SSE 都投影同一事件查询，SSE 不增加业务事件类型或持久状态。

### 流式输出与失败正文

Provider 只有 `stream(request)` 一个异步流入口。5.1 将内部完成结果升级为 `ProviderCompleted(ModelTurn(...))`：`kind="text"` 表示文本回合，`kind="tool_calls"` 表示结构化工具请求回合，后者不代表工具已执行或 Run 已完成。`ProviderRequest` 可携带不可变工具说明、assistant 调用以及按 `tool_call_id` 关联的工具结果；输入输出与预算由 [messages.py](src/minimew/execution/messages.py) 统一校验。

无工具声明的普通聊天仍逐批产出 `ProviderDelta(content)`。工具模式先收齐整轮正文与参数碎片，校验完整 JSON 和结束原因后只返回一个回合结果，避免将中间文字或参数写入用户答案。5.2 已接入 Registry/Guardrail 和有界循环，5.3 已接入显式启用的knowledge_search；默认关闭知识能力的普通Run收到未声明工具的回合仍以 `PROVIDER_INVALID_RESPONSE` 失败。[5.1 实施记录](chapters/05-基础知识问答.md#51-实施记录2026-11-12) 保存内部协议的交付边界。

执行器要求完成结果之后正常结束迭代；只有分片而没有明确完成、完成后继续输出、空白最终答案或非法分片，都以 `PROVIDER_INVALID_RESPONSE` 终结。`ProviderCompleted` 是内部调用协议，不是直接发送给前端的成功事件，最终结果仍需事务提交。

每批正文最多 4096 字符，累计分片及最终答案各最多 100000 字符。每批追加都检查 Run 为 running、owner token 匹配和租约有效，持有 Thread → Run 锁，提交前再次检查资格。数据库还拒绝 pending/过期执行的分片、错误消息归属、畸形 payload 和超预算正文。每批是可由 Adapter 合并后的文本，不要求每个 token 单独提交；2.7 Adapter 将 SDK 的公开正文切分为受控分片。

流式调用失败时，从同一 Run 的已提交分片按序恢复正文，保存到原 assistant Message，并在同一终结事务写入 failed 消息、message.completed(status=failed) 和 run.failed。过期回收也保留该前缀。分片事务回滚不会推进 sequence，也不会成为可补读正文；数据库故障不会被包装成模型错误。失败正文留在业务历史，但不进入后续模型的成功问答上下文。

历史 API 可以读取失败正文和 failed 状态。“回答未完成”是第 3 章聊天 UI 根据状态显示的提示，后端不把提示混进正文。本步不改变现有前端交互，也不在默认开发环境自动注入 fake。

### 工具注册与有界执行（5.2）

[execution/tools.py](src/minimew/execution/tools.py) 定义不可变 ToolSpec/Registry、严格 ToolModel、Run-owned ToolContext 以及带 `tool-capability-and-resource-v1` policy identity 的 GuardrailDecision。参数/结果禁止额外字段和隐式类型转换，结果JSON最多12000字符；执行前根据服务端能力和资源谓词产生ALLOW/DENY决定。当前没有审批状态或持久工具审计，不冒充完整控制面；模型参数不能提供或扩大用户身份、能力。

[RunExecutor](src/minimew/execution/service.py) 接收服务端显式注入的注册表和能力，默认两者为空。同一Run最多4次实际模型请求（包括重试）、6次串行工具调用，共用原绝对deadline。整轮先校验参数、权限、调用ID、上下文和剩余额度，再逐次检查执行资格、重新授权和调用；当前工具结果只放进本Run内存上下文，以原调用ID回传冻结模型，最终文本仍交原终结事务。模型重试只复用当前请求，不重跑已经成功的工具；工具无隐式重试。取消、过期owner、资格读取故障及吞取消的迟到返回均不能推进下一工具。

工具参数/权限/执行/结果错误、Agent次数或总期限耗尽有明确稳定错误码，沿用 `run.failed`/`message.completed`，未新增工具进度事件。MiniMew Event真源及生成的Python/TS、正式路由导出的OpenAPI及TS消费者已同步，前后端应一起更新，避免旧严格消费者拒绝新增错误码。无需数据库迁移或依赖安装。

5.3已将真实knowledge_search接入同一执行链，Evidence/引用留5.4，工具过程展示留5.5；5.2历史测试只注入受控handler，未新增产品用假检索。进程退出仍按当前中断/过期终结规则处理，不宣称已能从Checkpoint恢复工具循环。完整[5.2源码导航与验证](chapters/05-基础知识问答.md#52-实施记录2026-11-12)保留当时交付边界。

### 已发布知识工具（5.3）

在本机`MiniMew/.env`中添加或修改，然后重启API：

```ini
MINIMEW_KNOWLEDGE_ENABLED=true
```

```powershell
uv run --env-file .env --no-sync uvicorn minimew.app:create_app --factory --host 127.0.0.1 --port 8001
```

默认false保持普通聊天，不加载本地编码模型或连接Milvus。开启后复用第四章已准备的embedding/vector两个extra和本地轻量缓存；API启动加载bge-small CPU模型、只读检查独立Milvus连接，资源缺失时明确启动失败，不下载权重或静默移除工具。索引仍由独立Worker构建；API不创建集合、不上传或重建向量。API与Worker同时运行各占一份模型内存，模型缓存文件可复用。`/health/ready`仍只查询PG，启动检查不等于持续检测检索资源健康。

[knowledge.py](src/minimew/knowledge.py)提供唯一知识Tool：模型只能提交严格字符串query，服务端从ToolContext取得用户。每个Run由Registry绑定独立KnowledgeSession；首次知识调用取得published scope，后续调用复用，即使中途发布新版也不混用，下一Run重新读取current。沿用正式published_scope/encode_query/search_published及CandidateIndex，当前只支持已验证的轻量编码空间，不用M3向量查询旧索引。

[knowledge_runtime.py](src/minimew/knowledge_runtime.py)由API的execution_lifespan创建/关闭，管理本地模型、连接和单线程准入。同一时刻仅有一个同步知识操作，等待者可取消；已经开始的CPU/RPC操作在原线程完成后才归还槽，连接关闭排在在途操作之后。Run私有用户范围/候选不保存在共享Runtime里。Dispatcher先停止和排空Run，再关闭这些资源。

结果包含`status=ok/no_documents/no_matches`、matches和limited：每项保留原文、Document/Version/chunk/section标识、字符/行位置、内容hash与相似度；不把embedding_text、向量或原始数据库对象交给模型。默认top-k=3，按整体JSON预算保留完整片段，省略额外命中时标limited，不截断原文拼出虚假完整段落。健康空结果和知识编码/检索/范围/结果/输入长度故障有不同语义；5个新错误码已从Event真源同步生成至Python/TS及OpenAPI/API TS。

开启后模型可使用knowledge_search，工具模式仍整轮缓冲，不公开中间文字；普通模式保持流式。模型是否选择调用及真实答案质量未进行收费在线验收，候选命中不保证答案正确。5.4已接入下节的Evidence/引用绑定，工具过程及可点击引用UI留5.5，没有新的聊天入口或Checkpoint恢复。

本包212个不同用例通过（45新+167旧），另有真实CPU/Milvus隔离检查1次通过：真实独立Worker发布、正式API知识资源/Tool/Run查询、首位餐费150元及正确版本；回答Provider受控，收费调用0，精确清理1个自有scope及临时库。可复现命令：

```powershell
uv run --env-file .env --no-sync python scripts/check_knowledge.py
```

报告位于忽略的`.artifacts/knowledge-check.json`，不包含Key/登录Token。脚本仅使用随机临时PG、临时源目录和自身登记的精确向量scope，不删除集合或操作开发任务；原通知通道不属于此检查。完整[5.3职责框架与运行记录](chapters/05-基础知识问答.md#53-实施记录2026-11-12)保存通过、复跑、失败修正和未执行范围。

### Evidence与混合回答（5.4）

先在MiniMew目录显式运行`uv run --env-file .env --no-sync alembic upgrade head`，0013迁移新增answer_materials及Message/Run关联约束，不改写旧消息、Journal或文档；旧消息不会补造引用。历史证据不可用降级迁移直接删除。

知识工具返回的每个片段增加knowledge_base_id和Run内citation_id，例如E1；来源原文、版本、字符/行位置和hash仍来自正式检索。目录按实际回传的完整片段登记、跨检索去重，最多18份且序列化累计24000字符，每次工具结果仍不超过11000字符；整个模型上下文仍受60000字符上限约束，含服务端指令。

实际执行知识工具后，服务端追加混合回答指令。最终文本可包含`## 文档依据`与`## 模型补充（未由本次资料验证）`两个分区，顺序固定、空分区省略。文档依据每段须有来自本Run的`[E1]`等引用；模型补充不能挂文档引用。没有资料时只输出明确标识的补充；普通聊天不强制套用该版式。服务端检查格式和来源身份，不承诺自动证明语义支持关系；不合规使用既有PROVIDER_INVALID_RESPONSE失败，不额外发起纠错调用。

最终Message正文、引用映射、所引用的原文快照和Run终态同事务保存；取消、过期或失去owner资格不能留下成功引用。正文只属于Message，answer_materials不重复存储权威正文，也不实现Checkpoint恢复。

`GET /v1/threads/{thread_id}/runs/{run_id}/answer`需Access Token，按用户/Thread/Run归属读取；返回run_id、message_id、status、materials。materials含sections、citations、evidence，位置为正文Unicode字符的0基[start,end)，行位置为来源原文1基。没有引用资料的旧消息、普通聊天或未成功的Run返回materials=null；健康知识查询后仅补充的成功答案有sections而citations/evidence为空。读取故障返回503 ANSWER_READ_UNAVAILABLE，不能假装空资料。

message.completed v1保持message_id/status/content，SSE继续重放Journal；5.5前端已在完成与历史路径接入同一答案接口。查看旧答案不依赖当前文档指针或旧Milvus向量。OpenAPI与API TS由正式路由重新生成；5.4本身未增加Event类型，5.5的观测扩展见下节。源码职责与验收见[5.4记录](chapters/05-基础知识问答.md#54-实施记录2026-11-13)。

### 引用界面与工具过程（5.5）

显式升级到0014并重启API，前后端需一起更新。0014只扩展Journal允许的类型并增加工具事件guard，不改写旧消息/引用/Journal，不为旧Run补造观测。Run Event v1按现有扩展规则增加tool.started/tool.completed/tool.failed，保持旧事件与message.completed形状不变；生成Python/TS、OpenAPI/API TS及浏览器严格解析器同步。旧客户端遇到新事件应更新，不能忽略未知事件推进游标。

执行器在Registry/Guardrail检查后，通过原Repository短事务保存工具开始、结束/失败事件与Outbox。公开payload仅含tool_call_id、tool_name，失败另有受控error_code，不含参数、原始结果或私有推理。按Run/调用ID/事件阶段持久幂等；每次观测保存最多2次尝试、共用原期限，重试不会重新执行handler。数据库guard检查运行/租约/取消与先开始后结束，原Run终态仍由原事务拥有。

聊天页使用[答案解析](frontend/src/answers.ts)校验来源身份、分区和Unicode码点位置，[chat store](frontend/src/stores/chat.ts)按Run合并在途请求并缓存当前用户/Thread的材料；换账号/对话清除状态、拒绝迟到回填。只有校验过的引用可点击，[AnswerBody](frontend/src/components/AnswerBody.vue)显示保存的章节、版本、行号及原文，并区分模型补充。资料读取失败保留正文和已完成状态，可独立重试。

[ToolActivity](frontend/src/components/ToolActivity.vue)展示公开工具状态，实时数据来自原SSE/reducer；历史详情打开时通过既有/events分页重放同一Journal，不新增进度接口或执行状态表。工具结束不等于Run结束；失败/取消终态收束未闭合工具。旧Run没有观测时只说明“没有已保存的工具记录”，不推断当时没执行工具。沿用既有页面样式，无HTML注入渲染、新UI依赖或LangGraph接入。源码框架与定向验证见[5.5记录](chapters/05-基础知识问答.md#55-实施记录2026-11-13)。

### 第五章固定案例与真实浏览器验收（5.6）

[固定案例](examples/rag/chapter05-cases.json)由`uv run --no-sync pytest -q tests/test_knowledge_cases.py`复用正式知识工具/执行器，验证有限的引用、补充、空资料和故障契约；回答模型受控，不是语义质量评测。

```powershell
$env:MINIMEW_KNOWLEDGE_BROWSER_TESTS='1'
uv run --env-file .env --no-sync python scripts/check_documents_frontend.py
```

该入口使用已有轻量模型缓存及独立Milvus，不下载模型；创建临时库/源目录/端口及正式API和独立Worker，只替换回答Provider，收费0。浏览器验证150元v1与200元v2的历史/新Run引用、刷新工具过程、取消及换账号无资料；结束停止自有进程、精确删除并核验自有scope为空，不删集合或消费开发任务。报告为忽略的.artifacts/document-browser-*/report.json，截图为.artifacts/knowledge-desktop.png与knowledge-mobile.png，均不随Git同步。

本收尾包259个不同用例通过（33新+226旧、0 skipped），包括174 Python、82 Vitest及3 Playwright（2项真实API，1项模拟HTTP）；没有重复累加复验或5.4的352项。详见[5.6实际运行记录](chapters/05-基础知识问答.md#56-固定案例与收尾验收2026-11-13)、[结章总结](chapters/05-基础知识问答.md#结章文字总结2026-11-13)和[实际代码框架](chapters/05-基础知识问答.md#本章结束时的代码框架2026-11-13)。复杂检索/语义评判、Checkpoint和评测平台仍按后续章节推进。

### 事件分页查询

使用当前登录用户的 Bearer Access Token 请求：

```http
GET /v1/threads/{thread_id}/runs/{run_id}/events?after=0&limit=100
```

| 字段 | 含义 |
| --- | --- |
| `after` | 当前 Run 已按序处理的最后一个 sequence，首次为 0；不同 Run 各自维护 |
| `limit` | 每页 1–100 条，默认 100 |
| `items` | 按 sequence 升序返回的已提交事件，条件为 sequence > after |
| `next_cursor` | 本页最后一条事件的 sequence；空页保持原 after，不推进 |
| `has_more` | 本次查询是否还有下一页；false 不代表 Run 已结束 |

已读第 2 条，第一页只返回第 3 条时，下次用 after=3 继续读取。空页可发生在模型仍生成时，成功/失败须以对应持久终态为准。超出当前日志末尾的游标返回 422（EVENT_CURSOR_AHEAD）；未登录返回 401，他人的 Thread/Run 或不匹配的资源组合统一返回 404。响应保持 Cache-Control: no-store，不公开执行凭据、租约或模型配置。

启动中的旧后端进程需要重启才能加载新增路由；迁移由上述显式命令执行，不在应用启动时隐式运行。

### SSE 持续订阅与补读（2.5）

在同一 Run 下建立只读订阅：

```http
GET /v1/threads/{thread_id}/runs/{run_id}/events/stream?after=4
Authorization: Bearer <Access Token>
Accept: text/event-stream
```

握手复用正式验签与用户查询，再校验 Thread/Run 归属。前端后续用 fetch 带 Authorization 头连接，并以最后已按序处理的 sequence 维护 after；本步不新增浏览器订阅逻辑，也不把 Token 放 URL。显式 after 是唯一恢复输入，不另外处理 Last-Event-ID。每个数据帧的 event 字段为原事件 type，data 为完整契约 JSON，空行结束该帧；不发送可选 id 字段，也不改变原事件的 event_id/sequence。

查询在内部同时返回本次 Run 的状态与事件尾序号，并把分页限定到该尾序号；外部 JSON 分页结构保持原样。已成功的 Run 也会先补齐客户端缺少的正文和终态事件，再结束响应。若客户端 after 已到持久终态末尾，直接返回 204；这不是新产生的成功/失败事实。非终态空页继续等待，不把 EOF、HTTP 200 或 has_more=false 当作 Run 完成。

每页最多 100 条，读完关闭 Session/事务后才发送或等待。空闲时通过 Redis 等待进度提醒，默认最多 5 秒补查一次 PostgreSQL；通知到达可提前补查，积压页立即读完。没有业务事件时，每 15 秒发送 `: keep-alive` 注释心跳，不入库、不占业务序号。发送等待最多 10 秒，订阅最多 300 秒，并受已验签 Access 到期时间限制。每条订阅有独立业务游标与等待任务，按发送背压推进，不维护无界事件队列。Windows 单调时钟可能较粗，到期等待唤醒后再复核 UTC 有效期，发送前仍检查过期。

Access 验签现在返回内部不可变的用户 ID/UTC 到期信息；CurrentUser 仍投影同一个正式身份依赖，登录、刷新及 `/auth/me` 的公开数据结构保持不变。到期时停止当前 SSE 的后续发送，执行器继续保存事件；客户端刷新 Access 后再订阅原 Run。连续连接的到期限制是 MiniMew 本步新增策略，参考 SuperMew 当前仅在建连时检查 Token。已开始的 HTTP 响应不在中途改成 401；只有尚未发送响应头时才可返回受控错误，也不会在响应头发送结果未知时尝试第二次发头。

握手未登录/过期返回 401，不存在或越权返回 404，非法或超前游标返回 422，存储读取不可用返回受控 503。200 SSE 和 204 响应带 no-store，流响应带 X-Accel-Buffering: no。流开始后读库失败、发送超时、客户端断开或服务端取消，只清理该订阅；不伪造空页、成功/失败 Event，也不调用 Run 终结接口。日志只记录稳定类别与 Run ID，不记录原始数据库异常或凭据。

2.5 传输实现沿用现有事件契约；2.6 增加 0009 Outbox 与 Redis 依赖，2.7 再接入自动模型执行。完整聊天显示和浏览器自动重连在第 3 章完成。重启后端才能加载新路由，本次验收不停止用户已有进程。

隔离验收（MiniMew 目录）：

按影响范围选择离线用例和集成入口。`check_models.py` 包含认证、执行、事件和 SSE PostgreSQL 用例，相关代码与环境未变时，不再额外重复专项集。专项入口同样复用迁移检查，只缩小用例范围；订阅生命周期的离线检查见 `tests/test_sse.py`。

```powershell
uv run --no-sync pytest -q tests/test_execution.py
# 身份上下文与 SSE 定向离线测试：
uv run --no-sync pytest -q tests/test_auth.py tests/test_sse.py
# 执行器专项集成入口：
uv run --env-file .env --no-sync python scripts/check_execution.py
# SSE 的真实 HTTP 流及读取边界专项：
uv run --env-file .env --no-sync python scripts/check_sse.py
# 包含原有认证、刷新、Thread/Run 与执行检查的完整临时库验证：
uv run --env-file .env --no-sync python scripts/check_models.py
```

这些集成入口只接受默认本地 MiniMew 服务器，创建自己拥有的随机临时数据库，注入 fake 调用正式执行器，完成或失败后删除临时库。SSE 测试还绑定自己的临时 localhost socket，使用独立 Uvicorn 和真实 HTTP 客户端验证增量发送、重连、终态分页、权限/到期、空闲断连和读库故障，并检查连接池及请求任务释放；结束后关闭自己创建的线程和端口。原有领取、输入、快照、错误、事务回滚、终态/Journal 保护及迁移保留检查继续运行。不会在开发库写入测试对话，不需要模型 Key，也不请求 DeepSeek；fake 通过不证明真实模型在线能力。

### Outbox 与 Redis 通知（2.6）

使用独立 Redis 7.4.12 官方 Alpine 镜像并锁定 digest，客户端为 redis-py 8.1.0；既有 Python 依赖版本未升级。Compose 映射 `127.0.0.1:56379`，数据卷为 `minimew_redis_data`。`MINIMEW_REDIS_PASSWORD` 使用与数据库/JWT 不同的随机密码，保存于忽略的 `.env`。入口脚本将密码转换为 SHA-256 ACL，原始密码不进入 Redis 启动参数或事件；修改该配置后应重建 Redis 容器并重启应用。

`execution/journal.py` 是唯一追加入口，在调用方同一事务中保存 Event 和 `event_outbox`。0009 的延迟约束拒绝缺少待办的新事件，待办关联已有 Event；历史 Journal 保留原样。未发布待办不可删除，已发布记录不可回退，最终 Message/Run/结束事件仍由执行器原事务决定。

`execution/outbox.py` 的发布任务随 FastAPI lifespan 运行：短事务读待办 → 释放数据库资源 → 发 Redis → 短事务确认。空闲扫描默认 0.5 秒，失败退避从 1 秒增加到最多 30 秒；关闭 SDK 隐式重试，重试由发布代码负责。单批最多 20 条、周期预算 10 秒，应用关闭时停止并等待任务。重启后扫描 PostgreSQL 的未完成待办，不依赖内存队列，不重新调用模型。

`execution/notifications.py` 使用每 Run 一条 Stream，保存完整公开 Event JSON；原序号映射为 `<sequence>-0`。发送和设置 TTL 以同一次 Redis Lua 操作完成，重复/迟到序号由已存在的同 Run 新进度提醒覆盖。Outbox 确认表示通知责任已完成，不承诺每条副本一直保留，也不表示浏览器已处理。Stream 近似保留最近 1000 条、空闲 24 小时过期；PostgreSQL 事实不受裁剪/过期影响。发布池最多 4 个连接，等待池最多 32 个，等待不会占满发布资源。

SSE 仅用 Redis 提醒唤醒后端，再走同一 Journal 查询；Redis 副本正文不直接转发。每条 SSE 的业务发送游标独立，内部另记已见提醒位置以避免重复唤醒造成空查循环，该位置不推进业务游标。Redis 启动时或运行中不可用，记录稳定错误类别，发布待办保留、SSE 有间隔补查；Run 的模型执行和 PostgreSQL 提交继续。`/health/ready` 仍只报告数据库探测结果，不代表 Redis 或模型健康。

现有后端进程需要重启以读取新增配置和运行逻辑。2.7 已接入自动模型执行，聊天页面在第 3 章接入。

```powershell
# 通知的定向离线检查：
uv run --no-sync pytest -q tests/test_notifications.py
# Outbox 事务与迁移，使用临时 PostgreSQL：
uv run --env-file .env --no-sync python scripts/check_outbox.py
# 真实 Redis + PostgreSQL + HTTP，使用脚本自己拥有的临时 Redis 容器：
uv run --env-file .env --no-sync python scripts/check_notifications.py
# 也可在上述命令后指定一个或多个 pytest 文件/用例，缩小复验范围。
```

默认离线/普通 PostgreSQL 用例显式注入无外部通知资源，不隐式访问 Redis；真实通知验收仅在上述入口开启。脚本只停止/重启/删除自己创建的 `minimew_test_redis_<uuid>` 容器和匿名测试卷，不停止开发 Redis、不执行 FLUSHALL。测试 Redis 在本次生命周期固定临时宿主端口，避免 Docker 自动 HostPort 在重启后变化。正式 Run Event Schema、OpenAPI 和前端生成类型无需变化。

## 真实模型与自动执行（2.7）

在本机 `.env` 增加 `MINIMEW_DEEPSEEK_API_KEY=你的密钥`，再按前文管理命令设置模型 Profile，最后重启后端。不要覆盖已有 `.env`。缺少或空白 Key 时账户/Thread 仍能使用，新的提问返回 `503 MODEL_CONFIGURATION_UNAVAILABLE`；同键同内容的旧 Run 仍优先返回。已填但错误的 Key 会使实际 Run 失败，不通过 readiness 冒充模型可用。

入口依赖为 `langchain-deepseek==1.1.1`；锁定 LangChain Core 1.7.4、OpenAI 集成 1.7.3、OpenAI SDK 3.44.0、LangSmith 0.14.17、httpx2 2.16.0。uv 解析时 SDK 从讨论候选 3.42.1 更新至兼容的 3.44.0；既有依赖版本保持。httpx2 供新 SDK 使用，既有 HTTP/测试继续使用 httpx 0.28.1。依据为 [LangChain DeepSeek 集成](https://docs.langchain.com/oss/python/integrations/chat/deepseek) 与 [DeepSeek 流式协议](https://api-docs.deepseek.com/api/create-chat-completion/)。

`DeepSeekProvider` 只接受 `https://api.deepseek.com` 的根路径或 `/v1`（默认 HTTPS 端口），关闭重定向，不继承宿主代理/默认 SDK 身份或外部追踪。快照中 provider 标识仍为 `openai_compatible`，内部采用专用 `ChatDeepSeek`。客户端属于每次调用，忽略 reasoning 元信息；无工具模式要求有效文本和stop结束，工具模式校验完整参数及tool_calls/stop与结果一致后返回模型回合。截断、缺失完成、未声明工具和畸形流均失败，模型回合仍须由执行器消费后持久提交。SDK 自动重试为 0。

RunExecutor 最多尝试 2 次；仅暂时性网络/上游错误、尚无已提交正文、原 owner/lease 有效且原预算足够时重试。初始等待 0.5 秒，较长 Retry-After 同样受剩余预算约束；快照、输入、Run 和 owner 不更换。已保存正文后发生故障则失败并保留该前缀；存储故障不触发模型重试。

调度器默认每 0.5 秒检查候选、每实例最多 2 个活跃执行，同 Thread FIFO、不同 Thread 并发。查询已排除被前序工作阻塞的后继项，实际资格仍由 claim 事务决定。每 5 秒有界扫描最多 100 个过期 running，保留前缀并失败；有效 running 不抢占、不重跑。关闭时先停止扫描，给活跃任务 5 秒完成，再取消并有界等待最多 7 秒清理；进程硬退出仍可能留下 running，后续按 lease 过期终结。上述上限按实例计算，不是全局并发配额。

可以在 `/docs` 登录后创建 Thread、POST Run，轮询 GET Run，或用带 Bearer 的客户端订阅 SSE。已有模型配置且 Key 就绪时，后台会自动执行；刷新浏览器不取消它。当前可通过取消 API 停止指定 Run；聊天输入框和停止按钮在第 3 章接入。

定向验证（在 MiniMew 目录）：

```powershell
# 受控真实 SDK 协议、重试与生命周期，不访问模型网络：
uv run --no-sync pytest -q tests/test_deepseek.py tests/test_execution_retry.py tests/test_dispatcher.py
# 临时库 + 独占 Redis + HTTP，含自动执行、顺序、并发、过期、幂等：
uv run --env-file .env --no-sync python scripts/check_runtime.py
# 允许一次小规模真实模型验收；仍使用临时库/Redis/端口，可能发生模型费用：
uv run --env-file .env --no-sync python scripts/check_deepseek.py --online
```

`check_runtime.py` 支持在命令后指定测试文件/用例，只做空临时库升级及相关用例，不重复历史迁移验收；历史迁移回归仍使用 `check_models.py`。在线脚本临时 Profile 使用 128 输出 token 和 60 秒预算，只发短提示；不改开发 Profile 或写开发对话。默认 pytest 显式关闭正式后台资源，自动驱动/联网只由对应验收入口恢复。2026-11-04 在线链路实际返回 `OK`，完成 5 条连续 Journal 事件与游标补读；这不代表生产环境或所有故障均已在线验证。

## 按 Run 取消（2.8）

迁移到当前 `head` 并重启后端后，在 `/docs` 登录/Authorize，调用 `POST /v1/threads/{thread_id}/runs/{run_id}/cancel`，使用原 Thread 和 Run ID，无需新消息正文或新的幂等键。聊天页面及按 Run 停止按钮已在第 3 章接入。

| 持久状态 | 取消接口结果 |
| --- | --- |
| pending | 200，直接保存 cancelled；保留原消息对，不领取执行、不调用模型、不伪造 owner/租约/run.started |
| running | 202，保存 `cancel_requested_at` 和请求事件；Run 仍为 running，客户端可显示“取消中” |
| 已有取消请求 | 返回当前处理进度，请求时间和请求事件不重复写入 |
| cancelled / succeeded / failed | 200，返回原终态，保持历史不变 |
| 存储不可用 | 503 `RUN_CANCELLATION_UNAVAILABLE`，恢复后查询原 Run 或重试同一取消请求 |

503 可能来自提交确认丢失，不能据此判定请求必未入库，也不能把原 Run 改成 failed。接口沿用身份与归属校验：未认证 401，越权/不存在 404，受控响应 no-store；取消不依赖当前模型配置或 API Key。GET Run 增加可空的 `cancel_requested_at`；状态集合增加 `cancelled`，没有单独持久化的 cancelling 状态。

请求与结果均沿用原 Journal/Outbox。新事件为 `run.cancel_requested`、`run.cancelled`，取消的 assistant Message 和 `message.completed` 状态均为 cancelled，Run.error_code 为 null。最终正文从已提交的 message.delta 恢复；pending 的取消正文为空。用户消息、消息 ID、Thread 标题和冻结快照保持，取消轮次可查历史，但不进入下一轮模型的成功问答上下文。用同一幂等键和正文再次 POST 提问仍返回原取消 Run；新的提问意图用新键。

执行器在模型调用前完成初始控制检查，然后独立监视本 Run 的取消和资格状态；默认 `MINIMEW_CANCELLATION_POLL_SECONDS=0.5`，查询后即释放 Session，不等待下一批模型正文。取消请求可由其他后端进程接收，owner 通过共享 PostgreSQL 发现；Redis 仍只用于 SSE 提醒，停机时不妨碍取消监视。SSE 读完取消终态后正常关闭，原游标补读和已消费尾部的 204 保持。

请求、正文和终态写入统一按 Thread → Run 加锁。取消先提交，后续正文被拒绝、结果按取消收尾；成功或失败终态先提交，后来的取消保持原结果。普通 owner 终结仍检查 token/有效 lease；过期终结用独立事务及数据库过期条件裁决：有取消标记则 cancelled，否则 EXECUTION_EXPIRED，均保留前缀、不重跑模型。

取消监视读库失败会停止本次模型读取，并有界尝试保存 `EXECUTION_STORAGE_UNAVAILABLE`（已接受取消时优先取消）；存储持续不可用可能保留 running，恢复后由过期路径终结。模型读取与监视任务按本 Run 清理，有界等待后再请求停止并收集异常；生成和重试不获得新 deadline。此处保证业务状态与写入资格，不承诺撤回远端已发生的生成或计费。

本步沿用现有依赖；新增 0010 的状态/不可变/前缀约束、取消请求与终态唯一性，历史迁移不改写。Event Schema 真源、Python/TS 与 HTTP OpenAPI/类型同步生成。

```powershell
# 离线取消控制与原执行器重试回归：
uv run --no-sync pytest -q tests/test_cancellation.py tests/test_execution_retry.py
# 临时 PostgreSQL：取消原子性、竞争、过期与受控真实 SDK 清理：
uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_cancellation_pg.py
# 独占 Redis + 真实 HTTP + 两个独立 OS 进程；受控 Provider，不请求真实模型：
uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_cancellation_http.py
# 从真实 0009 旧事实升级，校验四种原状态/快照/正文/事件/Outbox 原样保留：
uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_cancellation_migration.py
```

`check_models.py` 的默认 PG 验收已包含取消；定向复验使用上述入口，避免每次重复历史迁移。双进程验收通过本机临时端口和 stdin 停止自己创建的进程，不增加测试 HTTP 路由；只在随机临时库的显式入口注入受控 Provider。本步未再次执行真实 DeepSeek 在线取消或浏览器 UI 验收；实际首次/复验结果见交接记录。

## 导航

- [分阶段技术、实施步骤与理解题](LEARNING_PLAN.md)
- [第 2 章：持久化普通对话计划、总结与代码框架](chapters/02-持久化普通对话.md)
- [第 3 章：最小聊天前端子项安排](chapters/03-最小聊天前端.md)
- [章节计划、文字总结与代码框架模板](CHAPTER_TEMPLATE.md)
- [学习互动与编码前确认规则](AGENTS.md)
- [Agent 跨设备交接与当前进度](AGENT_HANDOFF.md)
- [SuperMew 正式领域语言](../CONTEXT.md)
- [SuperMew 当前能力](../README.md)

## 九阶段路线

| 阶段 | 实现范围 | 验收标准 |
| --- | --- | --- |
| 1. 工程与领域骨架 | FastAPI、Vue、数据库迁移、配置、身份隔离、领域模型和 Contract | 空库可迁移；接口可鉴权；前后端共享契约 |
| 2. 持久化普通对话 | Thread、Message、Run、模型快照、Provider 调用、Event Journal、SSE | 能完成一轮对话；重复请求不会创建重复 Run；断线可重放 |
| 3. 最小聊天前端 | 登录、Thread 列表、输入框、流式正文、Run 状态、取消 | 刷新能恢复历史；重复 Event 不重复追加；不同 Run 不串消息 |
| 4. 文档入库与发布 | Document、不可变 Version、持久 Index Job、独立 Worker、解析与向量索引 | 上传后可查询进度；新版本构建失败时旧版本仍可检索 |
| 5. 基础知识问答 | 最小 Tool Registry、Guardrail、知识库 Tool、Dense 检索、Evidence、答案引用 | 问题能基于已发布文档回答；引用能定位具体文档版本 |
| 6. 完整 RAG 策略 | BM25、RRF、Auto-merging、Rerank、证据评判、一次重写、复杂问题拆解 | 固定案例可以验证各条路线；空结果和 Provider 故障明确区分 |
| 7. HITL 与故障恢复 | Checkpoint、暂停、同一 Run 恢复、取消竞态、lease/fencing、过期 owner 处理 | 人工回答后继续原执行；旧 owner 不能写入；终态只产生一次 |
| 8. RAG 评测闭环 | Dataset、fingerprint、Observation、Gate、baseline、Evaluation Job/Worker、工作台 | 同一数据与配置可比较结果；评测复用正式 RAG；失败案例可追踪 |
| 9. 扩展能力与交付 | Skill、模型与能力控制面完整 UI、Web Research、只读 SQL、Sandbox、生产部署 | 权限拒绝发生在执行前；API、Indexing Worker、Evaluation Worker 可独立运行；真实基础设施检查通过 |

## 学习方式

按 [最新分层节奏](AGENTS.md#分层教学与推进节奏2026-11-09) 推进：工程底座合并工作包、简要讲解，一次确认范围后连续实现并集中验收；Agent/RAG、分块与 Embedding、LangGraph、HITL 和评测作为学习重点，围绕关键决策保留场景讨论。

智能化部分按以下顺序进行：

```text
解释问题与技术职责
→ 用具体场景提问
→ 用户解释与提问
→ 补足理解缺口
→ 确认本次实现范围并获准开始
→ 小步编码与验证
→ 讲解代码并复盘
```

不会一次生成九阶段代码。文档中的技术与步骤是待逐阶段讨论的方案，不表示依赖已安装或实现已完成。

章节计划、结章文字总结与源码职责梳理仍保留；工程提速不减少必要功能或验证，也不要求逐个补答旧基础题。

## 当前进度

阶段、已通过的理解检查、待答问题与编码授权统一记录在 [Agent 交接文档](AGENT_HANDOFF.md#当前检查点)，继续学习前先读取。换设备流程也在该文档中。

## 参考范围与不变量

MiniMew 后续使用独立入口和配置，不接入 SuperMew 的生产执行链。其核心对象沿用 Thread、Message、Run、Event、Document Version 和 Model Snapshot 等正式术语。

阶段可以逐步扩展能力，但不得在早期引入之后需要推翻的事实来源：

- PostgreSQL 保存持久事实，SSE 是 Event Journal 的投影。
- 一个 Run 拥有一个用户 Message 和一个 assistant Message。
- Run 创建时固定模型快照，恢复不重选当前模型。
- 文档只检索已发布版本，失败 candidate 不替换 current。
- 外部服务故障与健康空结果必须区分。
- Tool 执行前完成权限与 Guardrail 检查，Secret 不进入公开状态。

本目录实现不变更上级 Contract、迁移、评测资产或应用行为。
