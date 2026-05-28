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

### 客户端环境变量优先级（按 provider 区分）

`askd_client.py` 是共享层，不同 provider 的兼容别名不同：

| 优先级 | 通用名 | Codex 兼容别名 | Gemini 兼容别名 | OpenCode 兼容别名 |
|--------|--------|----------------|-----------------|-------------------|
| 1 (canonical) | `CCB_SESSION_ID` | `CCB_SESSION_ID` | `CCB_SESSION_ID` | `CCB_SESSION_ID` |
| 2 (legacy alias) | — | `CODEX_SESSION_ID` | `GEMINI_SESSION_ID` | `OPENCODE_SESSION_ID` |
| 3 (fallback) | `caller_pane_id` | `caller_pane_id` | `caller_pane_id` | `caller_pane_id` |

客户端读取逻辑（`askd_client.py` 中，按 provider spec 选择对应的 legacy env）：

```python
ccb_session_id = (
    os.environ.get("CCB_SESSION_ID") or
    os.environ.get(spec.legacy_session_env) or  # CODEX/GEMINI/OPENCODE_SESSION_ID
    ""
).strip() or None
```

`ProviderClientSpec` 需新增 `legacy_session_env` 字段：
- `CASK_CLIENT_SPEC.legacy_session_env = "CODEX_SESSION_ID"`
- `GASK_CLIENT_SPEC.legacy_session_env = "GEMINI_SESSION_ID"`
- `OASK_CLIENT_SPEC.legacy_session_env = "OPENCODE_SESSION_ID"`

如果 `CCB_SESSION_ID` 和 legacy alias 都存在但值不同，输出警告并使用 `CCB_SESSION_ID`。

### 父目录查找保留

保留现有 `find_project_session_file()` 的"从 cwd 向父目录逐级查找"语义。用户在项目子目录中运行 `cask` 时仍能正确找到 session 文件。每一层目录都做同样的路由判断（精确匹配 → fallback → 两阶段扫描），某一层找到唯一匹配即返回。

### 精确匹配语义

"精确匹配 `ccb_session_id`"是指匹配 session 文件中的 **CCB launcher session id**（即 `data["session_id"]` 字段），**绝不匹配 agent 自身的 native session id**（`codex_session_id` / `gemini_session_id` / `opencode_session_id`）。

兼容旧 session 文件：如果 session 文件中没有 `ccb_session_id` 字段，回退匹配 `session_id` 字段（旧格式里两者值相同）。

### Daemon 路由逻辑

```
if ccb_session_id 存在:
    精确匹配 session 文件中的 ccb_session_id（不匹配 native session id）
    匹配不到 → 直接报错（禁止 fallback）

elif caller_pane_id 存在:
    registry 查 ccb_session_id → 精确匹配 session 文件
    匹配到后校验：
      1. session 文件存在
      2. active=true && 无 ended_at
      3. registry 中的 work_dir_norm 和当前请求 work_dir 一致（防止跨项目误路由）
    校验失败 → clear_provider_registry_fields()（仅清理该 provider 的字段），视为 miss

else:
    两阶段扫描（在当前目录及父目录逐级查找，每层执行）：
    阶段 1（轻量，在 session_utils 中完成）：
      按 session 文件过滤 active=true && 无 ended_at
      0 个 → 继续向父目录查找 / 最终报 no active session
      1 个 → 直接用
      >1 个 → 返回 candidates 列表，交给上层做阶段 2
    阶段 2（pane liveness probe，在 provider-specific load_project_session 层完成）：
      调用 backend.list_panes() 拿全量 pane snapshot（backend-agnostic）
      - WezTerm: wezterm cli list --format json
      - iTerm2: osascript list panes
      - tmux: tmux list-panes -a
      内存匹配候选的 pane_id/marker，剔除 pane 已死的僵尸候选
      剩 0 个 → 报 all candidates stale
      剩 1 个 → 用它
      仍 >1 个 → 报 ambiguity error
```

### session_utils 与上层的职责边界

```python
# session_utils.py 负责阶段 1（纯文件操作，不依赖 terminal backend）
def find_project_session_file(
    work_dir: Path,
    session_filename: str,
    *,
    session_id: str | None = None,
    caller_pane_id: str | None = None,
) -> Optional[Path]:
    """
    返回值语义：
    - Path: 唯一确定的 session 文件（精确匹配或阶段 1 单候选）
    - None: 无候选（no active session）
    如果阶段 1 有多个候选，不在此函数处理，
    由上层 load_project_session() 做阶段 2 liveness probe。
    """
```

当阶段 1 有多个候选时，`find_project_session_file()` 的行为：
- 如果有 `session_id`：精确匹配，匹配到返回 Path，匹配不到返回 None
- 如果有 `caller_pane_id`：通过 registry 间接匹配，成功返回 Path，失败返回 None
- 都没有：如果只有 1 个候选返回 Path；如果多个候选返回 None（上层需自行处理 ambiguity）

上层 `load_project_session()` 拿到 None 时，自行调用 `list_session_candidates()` + backend liveness probe 做阶段 2。

### 阶段 2 接口契约

**`session_utils.list_session_candidates()`**：

```python
def list_session_candidates(
    work_dir: Path,
    session_filename: str,
) -> list[Path]:
    """
    返回当前目录及所有父目录中的活跃候选 session 文件列表。
    仅做轻量过滤：active=true && 无 ended_at。
    按目录从近到远排序，同目录内按编号排序。
    用于上层 load_project_session() 做阶段 2 liveness probe。
    """
```

与 `find_project_session_file()` 的区别：
- `find_project_session_file()`：返回唯一 Path 或 None（已做完整路由判断）
- `list_session_candidates()`：返回所有活跃候选列表（不做路由判断，供上层做 liveness probe）

**`TerminalBackend.list_panes()`**：

```python
class TerminalBackend(ABC):
    @abstractmethod
    def list_panes(self) -> list[dict]:
        """
        返回所有 pane 的信息列表。
        每个 dict 至少包含：
        - pane_id: str
        - title: str（用于 marker 匹配）
        """
```

各 backend 实现：
- **WeztermBackend**：调用 `wezterm cli list --format json`（已有私有 `_list_panes()`，改为 public）
- **TmuxBackend**：调用 `tmux list-panes -a -F '#{pane_id}\t#{pane_title}'`
- **Iterm2Backend**：调用 osascript 获取 pane 列表

上层阶段 2 的 liveness probe 伪代码：

```python
def _resolve_ambiguity(candidates: list[Path], backend: TerminalBackend) -> Optional[Path]:
    """阶段 2：pane liveness probe，从多个候选中选出唯一存活的。"""
    panes = backend.list_panes()
    alive_pane_ids = {str(p["pane_id"]) for p in panes}
    alive_titles = {p.get("title", "") for p in panes}

    surviving = []
    for candidate in candidates:
        data = json.loads(candidate.read_text())
        pane_id = data.get("pane_id", "")
        marker = data.get("pane_title_marker", "")
        if pane_id in alive_pane_ids or any(marker in t for t in alive_titles):
            surviving.append(candidate)

    if len(surviving) == 1:
        return surviving[0]
    elif len(surviving) == 0:
        raise AmbiguityError("All candidates stale (no alive panes)")
    else:
        raise AmbiguityError(f"Multiple alive candidates: {[p.name for p in surviving]}")
```

---

## 第 2 节：协议层改造

### Request dataclass 增加 ccb_session_id

**CaskdRequest**（Codex，完整路由支持）：

```python
# lib/ccb_protocol.py
@dataclass(frozen=True)
class CaskdRequest:
    client_id: str
    work_dir: str
    timeout_s: float
    quiet: bool
    message: str
    ccb_session_id: str | None = None    # 新增
    caller_pane_id: str | None = None    # 已有
    output_path: str | None = None
```

**GaskdRequest / OaskdRequest**（Gemini/OpenCode，仅加 session_id 路由）：

```python
# lib/gaskd_protocol.py - GaskdRequest
# lib/oaskd_protocol.py - OaskdRequest
@dataclass(frozen=True)
class GaskdRequest:
    client_id: str
    work_dir: str
    timeout_s: float
    quiet: bool
    message: str
    ccb_session_id: str | None = None    # 新增（仅此一个字段）
    # 注意：不新增 caller_pane_id，Gemini/OpenCode 不支持 pane fallback
    output_path: str | None = None
```

### 客户端 payload 增加 ccb_session_id

```python
# lib/askd_client.py - try_daemon_request(spec, work_dir, message, ...)
# 按 provider 读取对应的 legacy env（见第 1 节 provider-specific alias 表）
ccb_session_id = (
    os.environ.get("CCB_SESSION_ID") or
    os.environ.get(spec.legacy_session_env) or  # CODEX/GEMINI/OPENCODE_SESSION_ID
    ""
).strip() or None

payload = {
    ...
    "ccb_session_id": ccb_session_id,
    "caller_pane_id": caller_pane_id,  # 仅 Codex 使用，Gemini/OpenCode 忽略
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
# 使用完整 ccb_session_id（格式 ai-<epoch>-<pid>），保证稳定唯一
pane_title_marker = f"CCB-{provider.capitalize()}-{self.session_id}"
# 例如: CCB-Codex-ai-1716890000-12345
```

不使用 PID 后缀（PID 可复用，不是稳定唯一身份）。`ccb_session_id` 本身包含 epoch + PID，碰撞概率极低。

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

### Registry 操作术语

Registry 有两种操作，必须严格区分：
- **clear_provider_registry_fields()**：清理 registry 文件中某个 provider 的字段（如 `codex_pane_id` / `codex_runtime_dir`），保留其他 provider 的字段。用于 provider 级操作（如 `ccb kill codex`）。
- **remove_registry(session_id)**：删除整个 registry 文件。仅用于 session 级操作（如 `cleanup()`、所有 provider 都已 inactive 时）。

不使用"provider 级 registry 清理"这种中间表述。

### cmd_kill 改造

```python
# ccb kill 找到 session 文件后：
# 1. 标该 provider 的 session 文件为 active=false
# 2. clear_provider_registry_fields(provider)（清理该 provider 的字段，不动其他 provider）
# 3. 检查同 session 下其他 provider 是否仍活跃（扫描 session 文件）
# 4. 全部 inactive 时才 remove_registry(session_id)
```

### cmd_kill 路由同步

cmd_kill 找 session 文件时目前只传 `caller_pane_id`。改为也按 `CCB_SESSION_ID(env) -> caller_pane_id -> 单实例 fallback` 走，和主路由保持一致。可选增加 `--session-id` CLI flag 用于显式指定目标实例。

### Fallback 校验中的 registry 清理

cask 通过 registry fallback 发现 codex pane 死了时，不能删整个 registry。改为 `clear_provider_registry_fields("codex")`（清理 registry 中 `codex_*` 相关字段：codex_pane_id / codex_runtime_dir / codex_input_fifo 等），保留其他 provider 的字段和 `claude_pane_id`。

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
# 保留已有的（兼容别名）：
env["CODEX_SESSION_ID"] = self.session_id
env["GEMINI_SESSION_ID"] = self.session_id
env["OPENCODE_SESSION_ID"] = self.session_id
```

Claude 子进程继承这些环境变量。Claude 内部通过 Bash tool 调用 cask/gask/oask/cpend 时，环境变量会自动传递到子进程。

---

## 文件变更清单

| 文件 | 改动 |
|------|------|
| `lib/ccb_protocol.py` | CaskdRequest 加 `ccb_session_id` 字段 |
| `lib/gaskd_protocol.py` | GaskdRequest 加 `ccb_session_id` 字段 |
| `lib/oaskd_protocol.py` | OaskdRequest 加 `ccb_session_id` 字段 |
| `lib/askd_client.py` | 读 `CCB_SESSION_ID` env + provider-specific legacy alias，payload 带 `ccb_session_id`，预检查用同一身份精确匹配 |
| `lib/providers.py` | `ProviderClientSpec` 新增 `legacy_session_env` 字段 |
| `ccb` | 注入 `CCB_SESSION_ID` env，`pane_title_marker` 唯一化（使用完整 session_id），`cmd_kill` 同步路由 + `clear_provider_registry_fields()` / 条件 `remove_registry()` |
| `lib/session_utils.py` | `find_project_session_file` 的匹配和 fallback 语义改造（签名已有 `session_id` 参数，无需新增），新增 `list_session_candidates()` 供上层阶段 2 使用，保留父目录查找语义 |
| `lib/terminal.py` | `TerminalBackend` 基类新增 `list_panes()` 抽象方法，`WeztermBackend._list_panes()` 改为 public，`TmuxBackend` / `Iterm2Backend` 新增 `list_panes()` 实现 |
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
- 旧 Claude 环境：`CODEX_SESSION_ID` / `GEMINI_SESSION_ID` / `OPENCODE_SESSION_ID` 保留为兼容别名
- 混合版本部署：新客户端发 `ccb_session_id`，旧 daemon 忽略未知字段（wire-compatible），单实例场景仍可工作。但**多实例可靠路由要求客户端和 daemon 同步升级**，否则旧 daemon 仍走 `candidates[0]` fallback，多实例会误投

## 不在本次范围

- Gemini/OpenCode 的 caller_pane_id fallback 路由（仅 Codex 支持）
- Registry TTL 缩短（当前 7 天，可通过校验层兜底）
- Slot 编号机制（已用 ccb_session_id 替代，不再需要 slot 概念）
