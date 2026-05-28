# CCB 同目录多实例支持设计方案

## 问题

CCB 在同一项目目录下同时启动多个 `ccb up` 实例时，会出现：
- **Daemon 路由错乱**：`/cask` 请求发给了错误的 Codex 实例
- **看起来像跨 tab 注入失败**：daemon 拿着错误的 pane_id 往错误的 pane 注入文字

## 根因

`lib/session_utils.py:204-205`：当 `caller_pane_id -> registry -> ccb_session_id` 链路任何一环断裂时，`find_project_session_file()` 静默返回 `candidates[0]`（第一个 session 文件）。同目录下有多个 `.codex-session*` 文件时，这等于"路由一旦缺失就发到错会话"。

## 验证结论

- **WezTerm CLI 跨 tab 注入可行**：`wezterm cli send-text --pane-id <id>` 可以跨 tab 操作同一个 WezTerm 实例内的任意 pane
- **Daemon 子进程也能调用 wezterm cli**：`CREATE_NO_WINDOW` 模式下 `wezterm cli list` 正常返回所有 pane
- **不需要 per-session daemon**：全局单例 daemon + 精确路由即可解决

## 方案：显式 session_id 主路由 + caller_pane_id fallback

### 核心原则

- `CCB_SESSION_ID` 是规范身份标识，`caller_pane_id` 降级为 fallback
- 显式 session_id 路由必须精确匹配，匹配不到直接报错
- 无身份时多实例必须报 ambiguity error，禁止静默选择
- Registry 是 session 级资源，不能在 provider 级路径上粗暴删除

---

## 第 1 节：路由机制

### 客户端环境变量优先级

```
CCB_SESSION_ID        ← 新增 canonical，ccb up 注入
CODEX_SESSION_ID      ← 已有，兼容别名
caller_pane_id        ← 已有，降级为 fallback
```

如果 `CCB_SESSION_ID` 和 `CODEX_SESSION_ID` 都存在但值不同，输出警告并使用 `CCB_SESSION_ID`。

### Daemon 路由逻辑

```
if ccb_session_id 存在:
    精确匹配 session 文件中的 session_id
    匹配不到 → 直接报错（禁止 fallback）

elif caller_pane_id 存在:
    registry 查 ccb_session_id → 精确匹配 session 文件
    匹配到后校验：session 文件存在 + active=true + 无 ended_at
    校验失败 → 清理该 provider 相关的 registry 字段，视为 miss

else:
    两阶段扫描：
    阶段 1（轻量）：按 session 文件过滤 active=true && 无 ended_at
      0 个 → 报 no active session
      1 个 → 直接用
      >1 个 → 进入阶段 2
    阶段 2（pane liveness probe）：
      一次 wezterm cli list 拿全量 pane snapshot，内存匹配候选的 pane_id/marker
      剔除 pane 已死的僵尸候选
      剩 0 个 → 报 all candidates stale
      剩 1 个 → 用它
      仍 >1 个 → 报 ambiguity error
```

注意：阶段 2 的 pane 存活校验不放在 `session_utils.py`（纯文件工具），而是放在 provider-specific 的 `load_project_session()` 层，或给 `find_project_session_file()` 注入 validator callback。

---

## 第 2 节：协议层改造

### Request dataclass 增加 ccb_session_id

三个协议文件各自加字段：

```python
# lib/ccb_protocol.py - CaskdRequest
# lib/gaskd_protocol.py - GaskdRequest
# lib/oaskd_protocol.py - OaskdRequest
@dataclass(frozen=True)
class CaskdRequest:
    client_id: str
    work_dir: str
    timeout_s: float
    quiet: bool
    message: str
    ccb_session_id: str | None = None    # 新增
    caller_pane_id: str | None = None
    output_path: str | None = None
```

### 客户端 payload 增加 ccb_session_id

```python
# lib/askd_client.py - try_daemon_request()
ccb_session_id = (
    os.environ.get("CCB_SESSION_ID") or
    os.environ.get("CODEX_SESSION_ID") or
    ""
).strip() or None

payload = {
    ...
    "ccb_session_id": ccb_session_id,
    "caller_pane_id": caller_pane_id,
    ...
}
```

### Daemon 提取并传递 ccb_session_id

```python
# lib/caskd_daemon.py - CaskdServer._handle_request()
req = CaskdRequest(
    ...
    ccb_session_id=str(msg.get("ccb_session_id") or "") or None,
    caller_pane_id=str(msg.get("caller_pane_id") or "") or None,
    ...
)

# _WorkerPool.submit()
session = load_project_session(work_dir,
    ccb_session_id=req.ccb_session_id,
    caller_pane_id=req.caller_pane_id)
```

gaskd_daemon.py、oaskd_daemon.py 同理。

### Gemini/OpenCode fallback 范围

当前 Gemini/OpenCode 的协议和 daemon 不收 `caller_pane_id`，也没有 registry-backed load path。本次改造：
- **Codex**：完整支持 session_id + caller_pane_id 双路由
- **Gemini/OpenCode**：只加 session_id 路由，pane fallback 暂不支持（在设计文档中明确标注）

---

## 第 3 节：Worker Key + Pane Title Marker

### pane_title_marker 唯一化

```python
# ccb - _start_provider_wezterm() / _start_provider_iterm2() / tmux 路径
short_id = self.session_id.rsplit("-", 1)[-1]  # 取 PID 部分
pane_title_marker = f"CCB-{provider.capitalize()}-{short_id}"
# 例如: CCB-Codex-12345
```

### compute_session_key() 改为优先 ccb_session_id

```python
def compute_session_key(session) -> str:
    # 优先用 CCB launcher session id（稳定唯一身份）
    ccb_sid = session.ccb_session_id  # 新 property
    if ccb_sid:
        return f"ccb:{ccb_sid}"
    # 其次 pane_id（运行时标识）
    pane = session.pane_id
    if pane:
        return f"codex_pane:{pane}"
    # 最后 marker（辅助重发现）
    marker = session.pane_title_marker
    if marker:
        return f"codex_marker:{marker}"
    return f"codex_file:{session.session_file}"
```

三个 `*ProjectSession` 类（CodexProjectSession / GeminiProjectSession / OpenCodeProjectSession）都加显式 `ccb_session_id` property，避免和 agent 自身的 native session id（codex_session_id / opencode_session_id）混淆。

---

## 第 4 节：Registry 清理

### 核心约束：Registry 是 session 级资源

registry 文件是"每个 CCB 实例一个文件"（按 `ccb_session_id`），不是"每个 provider 一个"。`ccb kill codex` 只杀 Codex provider，不能删整个 registry（会误伤同 session 下的 Gemini/OpenCode）。

### cmd_kill 改造

```python
# ccb kill 找到 session 文件后：
# 1. 标该 provider 的 session 文件为 active=false
# 2. 不删 registry（除非该 ccb_session_id 下所有 provider 都已 inactive）
# 3. 检查同 session 下其他 provider 是否仍活跃
# 4. 全部 inactive 时才 remove_registry(session_id)
```

### cmd_kill 路由同步

cmd_kill 找 session 文件时目前只传 `caller_pane_id`。改为也按 `CCB_SESSION_ID(env) -> caller_pane_id -> 单实例 fallback` 走，和主路由保持一致。可选增加 `--session-id` CLI flag 用于显式指定目标实例。

### Fallback 校验中的 registry 清理

cask 通过 registry fallback 发现 codex pane 死了时，不能删整个 registry。改为清理 registry 中 `codex_*` 相关字段（codex_pane_id / codex_runtime_dir 等），保留其他 provider 的字段。

### 异常退出

`cleanup()` 已有 `remove_registry(self.session_id)`，保持不变。但异常退出（进程被强杀、宿主崩溃、窗口直接消失）无法保证 cleanup 运行。因此 fallback 校验中的 stale-registry 检测仍然是必须的。

---

## 第 5 节：客户端预检查改造

### askd_client.py - try_daemon_request() 前的预检查

```python
# 现在：find_project_session_file(work_dir, '.codex-session')
# 改为：用同一身份精确匹配
session_file = find_project_session_file(work_dir, ".codex-session",
    session_id=ccb_session_id,
    caller_pane_id=caller_pane_id)
```

### maybe_start_daemon() 中的预检查同理

### bin/cask / bin/gask / bin/oask 的预检查

这三个脚本各自有"daemon 不可用时的最终预检查"（如 `bin/cask:159`），也要同步用 session_id 精确匹配。注意 payload 组装不在这三个脚本，而是在 `askd_client.py`。

### bin/cpend + lib/codex_comm.py 收口

这两处目前只读 `CODEX_SESSION_ID`。改为优先读 `CCB_SESSION_ID`，fallback `CODEX_SESSION_ID`，保持和新 canonical 一致。

---

## 第 6 节：环境变量注入

### ccb up 启动时

```python
# ccb - _start_claude()
env["CCB_SESSION_ID"] = self.session_id
# 保留已有的：
env["CODEX_SESSION_ID"] = self.session_id  # 兼容别名
```

Claude 子进程继承这些环境变量。Claude 内部通过 Bash tool 调用 cask/gask/oask/cpend 时，环境变量会自动传递到子进程。

---

## 文件变更清单

| 文件 | 改动 |
|------|------|
| `lib/ccb_protocol.py` | CaskdRequest 加 `ccb_session_id` 字段 |
| `lib/gaskd_protocol.py` | GaskdRequest 加 `ccb_session_id` 字段 |
| `lib/oaskd_protocol.py` | OaskdRequest 加 `ccb_session_id` 字段 |
| `lib/askd_client.py` | 读 `CCB_SESSION_ID` env，payload 带 `ccb_session_id`，预检查用同一身份精确匹配 |
| `ccb` | 注入 `CCB_SESSION_ID` env，`pane_title_marker` 唯一化，`cmd_kill` 同步路由 + provider 级 registry 清理 |
| `lib/session_utils.py` | `find_project_session_file` 加 `session_id` 参数，精确匹配 + 校验，ambiguity 两阶段过滤（阶段 2 不在此文件，通过 callback 或上层处理） |
| `lib/caskd_session.py` | `compute_session_key` 优先 `ccb_session_id`，加 `ccb_session_id` property，`load_project_session` 加 `ccb_session_id` 参数 |
| `lib/gaskd_session.py` | 同上 |
| `lib/oaskd_session.py` | 同上 |
| `lib/caskd_daemon.py` | 提取 `ccb_session_id` 传给 session loading |
| `lib/gaskd_daemon.py` | 同上 |
| `lib/oaskd_daemon.py` | 同上 |
| `bin/cask` | daemon 不可用时预检查用 session_id 精确匹配 |
| `bin/gask` | 同上 |
| `bin/oask` | 同上 |
| `bin/cpend` | 读 env 改为优先 `CCB_SESSION_ID` |
| `lib/codex_comm.py` | 读 env 改为优先 `CCB_SESSION_ID` |

## 向后兼容

- 单实例场景：无 `CCB_SESSION_ID` 时走 `caller_pane_id -> 单候选 fallback`，行为和现在完全一致
- 旧 Claude 环境：`CODEX_SESSION_ID` 保留为兼容别名
- 旧 daemon：新客户端发 `ccb_session_id`，旧 daemon 忽略未知字段（JSON payload），自动降级到旧路由

## 不在本次范围

- Gemini/OpenCode 的 caller_pane_id fallback 路由（仅 Codex 支持）
- Registry TTL 缩短（当前 7 天，可通过校验层兜底）
- Slot 编号机制（已用 ccb_session_id 替代，不再需要 slot 概念）
