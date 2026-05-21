# CCB 多会话支持设计方案

## 现状

CCB 在同一目录下只允许运行一个实例。`ccb up` 会在 `Path.cwd()` 下写入 `.codex-session`、`.gemini-session`、`.opencode-session` 三个固定名称的会话文件，第二个实例会覆写这些文件，导致第一个实例的窗格信息丢失、daemon 路由错乱。

## 两类使用场景

### 场景 A：同窗口分屏（已支持）
在同一 WezTerm 窗口中通过 `split-pane` 切分出 Codex/Gemini/OpenCode 窗格。用户同时看到 Claude + 多个 AI 助手，通过 `/cask`、`/gask`、`/oask` 交互。

```
┌──────────┬──────────┐
│  Claude  │  Codex   │
│  (主控)  │          │
├──────────┼──────────┤
│  Gemini  │ OpenCode │
└──────────┴──────────┘
```

### 场景 B：多 Tab/窗口并发（需实现）
同一项目目录下同时开发多个需求（如 feature-A 和 feature-B），每个需求启动独立的 CCB 会话（各自有 Claude + Codex），互不干扰。可以是：

- 同一 WezTerm 窗口的不同 tab
- 不同 WezTerm 窗口

```
Tab 1: Claude-A ←→ Codex-A     (需求 A)
Tab 2: Claude-B ←→ Codex-B     (需求 B)
```

两个会话在同一个 `E:\my-project` 目录下运行，但会话文件、窗格、daemon 路由必须完全隔离。

## 核心问题

要实现场景 B，需要解决以下问题：

### 1. 会话文件命名冲突
多个 CCB 实例在同目录下需要各自的会话文件。

### 2. 窗格标识不稳定
WezTerm pane ID 在窗格关闭/重建后会变化，CCB 写入会话文件的 pane_id 可能指向已死或错误的窗格。

### 3. Daemon 上下文隔离
CCB 的 daemon（caskd/gaskd/oaskd）是全局单例。同一个 daemon 需要能为不同 WezTerm 窗口/tab 的窗格注入文字。但 daemon 进程从某个窗口启动，其 `wezterm cli` 可能无法访问其他窗口的 pane。

### 4. Codex 会话隔离
同一目录下多个 Codex 实例需要独立的会话，避免日志文件混淆和响应串扰。

## 设计方案

### 核心思路：会话槽位（Slot）

每个 CCB 实例自动分配一个槽位编号（slot），从 0 开始递增。Slot 0 保持向后兼容（文件名、窗格标记不变），slot N（N>0）在文件名和标记后追加 `-N`。

### 槽位分配（原子性）

**问题**：两个 CCB 实例几乎同时启动时，可能都检测到 slot 0 空闲（TOCTOU 竞态）。

**方案**：使用独占文件创建（`O_CREAT | O_EXCL`）原子性地抢占槽位。每个槽位对应一个 sentinel 文件，能创建成功即抢占成功。

```python
def _claim_slot(provider: str) -> int:
    for slot in range(64):
        filename = f".{provider}-session" if slot == 0 else f".{provider}-session-{slot}"
        sentinel = Path.cwd() / f"{filename}.lock"
        try:
            fd = os.open(str(sentinel), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return slot   # 抢占成功
        except FileExistsError:
            # 检查对应 session 是否已死亡（active=false）
            session_file = Path.cwd() / filename
            if session_file.exists():
                data = json.loads(session_file.read_text(...))
                if not data.get("active", False):
                    sentinel.unlink()
                    # 重试抢占
                    ...
            continue
```

关键点：
- 用文件系统级别的原子操作（`O_EXCL`）消除竞态
- 死会话的槽位可回收（检查 `active` 标志）
- Cleanup 时删除 sentinel 文件

### 会话文件命名

| Slot | Codex | Gemini | OpenCode |
|------|-------|--------|----------|
| 0 | `.codex-session` | `.gemini-session` | `.opencode-session` |
| 1 | `.codex-session-1` | `.gemini-session-1` | `.opencode-session-1` |
| N | `.codex-session-N` | `.gemini-session-N` | `.opencode-session-N` |

### 窗格标记

Daemon 用窗格标题标记（`pane_title_marker`）来区分不同槽位的 worker：

| Slot | 标记 |
|------|------|
| 0 | `CCB-Codex` |
| 1 | `CCB-Codex-1` |
| N | `CCB-Codex-N` |

Daemon 的 `compute_session_key()` 优先使用标记生成 worker key，不同 slot 自动获得各自的 worker。

### 环境变量传播

`_start_claude` 设置 `CCB_SESSION_SLOT=N`，子进程 Claude 继承。`/cask` 等命令读取该变量，将 slot 写入 daemon 请求 payload。

```
ccb up → _detect_claim_slot() → slot=1
       → 写 .codex-session-1, marker=CCB-Codex-1
       → env["CCB_SESSION_SLOT"] = "1"
       → Claude 子进程继承
       → /cask 读 CCB_SESSION_SLOT=1
       → daemon payload {"slot": 1, ...}
       → daemon 加载 .codex-session-1
       → worker key = codex_marker:CCB-Codex-1
```

### Daemon 改造

daemon 请求协议中增加 `slot` 字段（默认 0）：

- `CaskdRequest` / `GaskdRequest` / `OaskdRequest` 加 `slot: int = 0`
- `load_project_session(work_dir, slot=N)` 加载对应 slot 的会话文件
- `SessionRegistry` key 从 `work_dir` 改为 `(work_dir, slot)`

### 窗格发现增强

**当前问题**：`ensure_pane()` 先检查 pane_id 是否存活，失败后尝试按标题标记搜索。但 Codex 启动后会将标题从 `CCB-Codex-1` 改为自己的标题（如 "Codex"），标记搜索失效。

**改进方案**：

1. **在 Codex 启动后重新注入标题**——由于 Codex 是 TUI 应用，不能简单追加命令。可通过定时向窗格发送标题设置序列的方式保持标记。方案成本较高。

2. **使用 WezTerm user-vars 代替标题**——`wezterm cli set-user-var` 功能可用但 CLI 接口有限。

3. **使用 pane_id 稳定性保证**——最简单的方案：
   - CCB 创建的窗格在会话存活期间保持不关闭
   - Cleanup 时才杀死窗格
   - `ensure_pane()` 仅在 pane_id 存活检查失败时才走 fallback

4. **推荐：pane_id + 定期存活检查**——配合 daemon 的定期存活检查（已有 `_check_all_sessions`），及时标记死窗格。同时增加 daemon 日志中更明确的错误信息。

### Daemon WezTerm 上下文问题

**问题**：daemon 从 Tab 1 启动，其 `wezterm cli` 可能无法访问 Tab 2 的窗格。之前的测试中手动 `wezterm cli send-text` 成功但 daemon 的注入失败，疑为 daemon 启动参数 `DETACHED_PROCESS` 导致 WezTerm 上下文丢失。

**已修复**：`askd_client.py` 中 `DETACHED_PROCESS` 改为 `CREATE_NO_WINDOW`，保留控制台附着。

**仍需验证**：daemon 进程能否可靠地向不同 tab 的窗格注入文字。如果 WezTerm CLI 跨 tab 有限制，可能需要每个 CCB 实例启动独立的 daemon（通过 `CCB_CASKD_STATE_FILE` 环境变量隔离 daemon 状态文件）。

### Cleanup 与 Kill

- `cleanup()`——根据 `self.slots` 标记对应槽位的会话文件为 inactive
- `ccb kill`——扫描所有槽位（0-63），杀死所有活跃会话

### 文件清单

需修改的文件（约 12 个）：

| 文件 | 改动 |
|------|------|
| `ccb` | 原子槽位抢占、槽位感知的会话文件/窗格标记/env var/cleanup/kill |
| `lib/caskd_session.py` | `find_project_session_file(work_dir, slot)` |
| `lib/gaskd_session.py` | 同上 |
| `lib/oaskd_session.py` | 同上 |
| `lib/ccb_protocol.py` | `CaskdRequest` 加 `slot` 字段 |
| `lib/gaskd_protocol.py` | 同上 |
| `lib/oaskd_protocol.py` | 同上 |
| `lib/askd_client.py` | 读 `CCB_SESSION_SLOT`，payload 带 slot |
| `lib/caskd_daemon.py` | 提取 slot，传给 session loading，SessionRegistry key 改 `(work_dir, slot)` |
| `lib/gaskd_daemon.py` | 同上 |
| `lib/oaskd_daemon.py` | 同上 |
| `bin/cask` / `bin/gask` / `bin/oask` | 回退路径 slot 感知 |
| `.gitignore` | 加 `.*-session-*` |

## 待验证问题

1. **WezTerm CLI 跨 tab 注入**——daemon 能否向其他 tab 的窗格注入文字？如果不能，需要每 tab 独立 daemon。
2. **Codex 同目录多会话**——Codex 是否真正支持同目录多个独立会话？之前的测试中两个 Codex 日志文件相同，需要确认隔离方案。
3. **窗格 ID 稳定性**——WezTerm pane ID 在什么情况下会变化？窗格被 `kill_pane` 以外的操作关闭时是否会重新编号？

## 实施优先级

1. **P0**：原子槽位抢占 + 会话文件隔离 + env var 传播 + daemon slot 路由
2. **P1**：daemon 跨 tab 注入验证 + 窗格发现增强
3. **P2**：`ccb kill` 多 slot 支持 + cleanup 完善
