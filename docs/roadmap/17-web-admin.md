# 阶段 17：Web 管理员与用户管理

状态：`COMPLETE`

## 目标

为现有多用户 Web 研究台提供一个仅管理员可见的管理界面。管理员可以直接创建朋友的账号并维护账号生命周期，不需要把邀请码或服务器环境变量交给朋友。

## 范围

- 为 `WebUser` 增加管理员标记和启用状态，并兼容已经存在的 `web_users` 表。
- 现有遗留环境变量账号首次迁移后自动成为管理员；也支持通过 `ASHARE_WEB_ADMIN_USERNAME` 指定管理员用户名。
- 新增 `/admin` 管理页面：查看用户列表、创建普通用户、启用/停用账号、重置普通用户密码、删除普通用户。
- 管理员操作全部经过当前管理员会话和 CSRF 校验；停用账号后既有会话也立即失效。
- 删除账号时同时清理该用户的持仓、关注池、模拟盘账户/订单和个人告警状态；公共行情快照不受影响。
- 在共享导航中仅给管理员显示“用户管理”入口。

## 非目标

- 不开放普通用户提升为管理员的页面操作；管理员角色通过遗留账号迁移或服务端环境变量配置。
- 不建设角色层级、组织、多租户计费、审计日志、批量导入或邮箱找回密码。
- 不允许管理员从后台修改遗留环境账号的密码；该账号密码仍由 `ASHARE_WEB_PASSWORD` 管理。
- 不允许管理员停用/删除自己，也不允许删除其他管理员，避免后台锁死。

## 设计决策

1. `WebUser.is_admin` 和 `WebUser.is_active` 使用非空布尔字段，默认分别为 `false` 和 `true`。`database.init_db()` 对已有 SQLite 表执行幂等的 `ALTER TABLE ... ADD COLUMN`，避免升级时丢失账号。
2. `ASHARE_WEB_ADMIN_USERNAME` 是可选的管理员种子配置；匹配该用户名的账号在登录时被提升为管理员。已有 `legacy_env=True` 的环境账号始终作为管理员入口，保证当前拥有者升级后可直接进入后台。
3. 管理员创建的新账号统一是普通、启用状态，密码仍使用 PBKDF2-SHA256 哈希。管理员重置普通用户密码时替换哈希；停用用户时 `authenticate_web_user()` 和 `load_web_principal()` 都拒绝该账号，从而使 Basic Auth、登录和旧 Cookie 一并失效。
4. 删除操作按 `user_id` 精确定位，并显式清理没有外键级联到 `User` 的模拟盘和告警数据。操作不触碰共享 `QuoteSnapshot`、日线和跨市场快照。
5. 管理页面采用服务端渲染和 PRG（Post/Redirect/Get），操作结果只通过短状态码回显，密码永远不回显或写入日志。

## 数据/API 变化

- `web_users` 新增 `is_admin`、`is_active` 两列。
- `GET /admin`：管理员用户列表和创建用户表单。
- `POST /admin/users`：管理员创建普通用户。
- `POST /admin/users/{user_id}/status`：启用或停用指定普通用户。
- `POST /admin/users/{user_id}/password`：重置指定普通用户密码。
- `POST /admin/users/{user_id}/delete`：删除指定普通用户及其个人数据。

## 风险与处理

- 旧 SQLite 表没有新增列：初始化时先建表再检查列，缺失列使用安全默认值补齐，并保留原有数据。
- 管理员误停用或删除自己：后端按当前会话用户 ID 拒绝，不依赖前端按钮隐藏。
- 停用用户仍持有 Cookie：每次页面认证重新查询数据库中的 `is_active`，旧会话立即失效。
- 删除用户遗漏模拟盘或告警数据：删除服务显式覆盖 `PaperAccount`、`PaperPosition`、`PaperOrder` 和 `AlertState`。
- 管理员密码由环境变量提供：后台显示遗留账号标记并隐藏重置操作，避免用户误以为已修改环境密码。

## 验收标准

- 管理员可以访问 `/admin`，普通用户访问返回 403，未登录用户仍被引导到登录页。
- 管理员可以创建新用户；新用户可以登录且默认没有管理员权限。
- 停用用户后，登录、旧 Cookie 和 Basic Auth 均不能继续访问；重新启用后可恢复登录。
- 管理员可以重置普通用户密码，旧密码失效，新密码生效；密码哈希不包含明文。
- 删除普通用户会删除其个人持仓、关注池、模拟盘与告警记录，但不影响其他用户和公共行情。
- 管理员不能停用或删除自己，也不能删除其他管理员。
- `ruff check .`、全量 unittest、`python -m compileall -q .` 通过。

## 实施记录

已完成：

- `WebUser` 新增 `is_admin` / `is_active` 字段；SQLite 初始化会为已存在的 `web_users` 表幂等补列。遗留环境账号自动成为管理员，`ASHARE_WEB_ADMIN_USERNAME` 可指定已有账号为管理员。
- 新增 `/admin` 及用户管理操作：直接创建普通用户、启用/停用、普通用户密码重置、删除用户及其个人数据；所有写操作均使用管理员绑定的 CSRF token。
- 停用账号会同时阻断密码登录、Basic Auth 和旧 Cookie；管理员自账号及其他管理员受到后端保护，不能被误删或锁死。
- 删除用户会清理持仓、关注池、模拟账户、模拟订单、模拟持仓和个人告警状态，不影响公共行情与研究缓存。
- 共享导航只对管理员显示“用户管理”入口；本地 Uvicorn 烟雾检查确认管理员页、创建表单、账号列表和管理员导航可渲染。
- 验证：`git diff --check` 通过；`ruff check .` 通过；`python -m compileall -q .` 通过；全量 unittest 共 112 项通过。

## 验证命令

```bash
ruff check .
python -m unittest discover -s tests -v
python -m compileall -q .
```
