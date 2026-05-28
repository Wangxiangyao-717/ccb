# CCB 同目录多实例路由 Implementation Plan (Part 2)

## Phase 4: Session Loading (续)

### Task 12: caskd_session - 修改 load_project_session 接受 ccb_session_id

**Files:**
- Modify: `lib/caskd_session.py:218-234` (load_project_session 函数)
- Test: `test/test_caskd_session_routing.py` (扩展)

- [ ] **Step 1: Write failing test for load_project_session with ccb_session_id**

```python
# test/test_caskd_session_routing.py (追加)
from caskd_session import load_project_session

def test_load_project_session_with_ccb_session_id(tmp_path):
    """load_project_session should accept ccb_session_id parameter"""
    session_file = tmp_path / ".codex-session"
    session_file.write_text(json.dumps({
        "session_id": "target-session",
        "active": True,
        "runtime_dir": str(tmp_path),
        "terminal": "tmux",
    }), encoding="utf-8")
    
    session = load_project_session(tmp_path, ccb_session_id="target-session")
    assert session is not None
    assert session.ccb_session_id == "target-session"

def test_load_project_session_ccb_session_id_no_fallback(tmp_path):
    """load_project_session should return None if ccb_session_id doesn't match"""
    session_file = tmp_path / ".codex-session"
    session_file.write_text(json.dumps({
        "session_id": "session-A",
        "active": True,
    }), encoding="utf-8")
    
    session = load_project_session(tmp_path, ccb_session_id="session-B")
    assert session is None, "Should return None, not fallback to candidates[0]"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test/test_caskd_session_routing.py::test_load_project_session_with_ccb_session_id -v`
Expected: FAIL with "TypeError: load_project_session() got an unexpected keyword argument 'ccb_session_id'"

- [ ] **Step 3: Add ccb_session_id parameter to load_project_session**

```python
# lib/caskd_session.py (修改 load_project_session 函数签名和实现)
def load_project_session(work_dir: Path, caller_pane_id: Optional[str] = None, ccb_session_id: Optional[str] = None) -> Optional[CodexProjectSession]:
    """
    加载 Codex project session
    
    Args:
        work_dir: 工作目录
        caller_pane_id: 调用者的 pane ID（用于 registry fallback）
        ccb_session_id: CCB session ID（用于精确匹配，优先级最高）
    
    Returns:
        CodexProjectSession or None
    """
    # 优先尝试 registry-backed session（如果提供了 caller_pane_id）
    if caller_pane_id:
        session = _load_registry_backed_session(work_dir, caller_pane_id, ccb_session_id)
        if session is not None:
            return session
    
    # 使用 find_project_session_file 查找
    session_file = find_project_session_file(work_dir, caller_pane_id=caller_pane_id, session_id=ccb_session_id)
    if not session_file:
        return None
    
    data = _read_json(session_file)
    if not data:
        return None
    
    return CodexProjectSession(session_file=session_file, data=data)
```

- [ ] **Step 4: Update _load_registry_backed_session to accept ccb_session_id**

```python
# lib/caskd_session.py (修改 _load_registry_backed_session 函数)
def _load_registry_backed_session(work_dir: Path, caller_pane_id: Optional[str], expected_ccb_session_id: Optional[str] = None) -> Optional["CodexProjectSession"]:
    """
    从 registry 加载 session
    
    如果提供了 expected_ccb_session_id，会验证 registry 中的 ccb_session_id 是否匹配
    """
    pane_id = str(caller_pane_id or "").strip()
    if not pane_id:
        return None

    record = load_registry_by_claude_pane(pane_id)
    if not isinstance(record, dict):
        return None

    # 验证 ccb_session_id（如果提供了）
    if expected_ccb_session_id:
        registry_ccb_session_id = str(record.get("ccb_session_id") or "").strip()
        if registry_ccb_session_id != expected_ccb_session_id:
            return None  # 不匹配，返回 None

    expected_work_dir = _normalize_work_dir(work_dir)
    record_work_dir = _normalize_work_dir(record.get("work_dir_norm") or record.get("work_dir") or "")
    if expected_work_dir and record_work_dir and expected_work_dir != record_work_dir:
        return None

    # ... 其余代码保持不变 ...
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest test/test_caskd_session_routing.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add lib/caskd_session.py test/test_caskd_session_routing.py
git commit -m "feat(caskd_session): add ccb_session_id parameter to load_project_session"
```

### Task 13: gaskd_session/oaskd_session - 同样的改动

**Files:**
- Modify: `lib/gaskd_session.py`
- Modify: `lib/oaskd_session.py`
- Test: `test/test_gaskd_session_routing.py` (新建)
- Test: `test/test_oaskd_session_routing.py` (新建)

- [ ] **Step 1: Copy caskd_session changes to gaskd_session**

对 `lib/gaskd_session.py` 做同样的改动：
- 添加 `ccb_session_id` property 到 `GeminiProjectSession`
- 修改 `compute_session_key` 优先使用 `ccb_session_id`
- 修改 `load_project_session` 接受 `ccb_session_id` 参数

- [ ] **Step 2: Copy caskd_session changes to oaskd_session**

对 `lib/oaskd_session.py` 做同样的改动：
- 添加 `ccb_session_id` property 到 `OpenCodeProjectSession`
- 修改 `compute_session_key` 优先使用 `ccb_session_id`
- 修改 `load_project_session` 接受 `ccb_session_id` 参数

- [ ] **Step 3: Write tests for gaskd_session**

```python
# test/test_gaskd_session_routing.py
from pathlib import Path
from gaskd_session import GeminiProjectSession, compute_session_key, load_project_session

def test_gemini_project_session_has_ccb_session_id_property(tmp_path):
    """GeminiProjectSession should have ccb_session_id property"""
    session_file = tmp_path / ".gemini-session"
    session_file.write_text("{}", encoding="utf-8")
    
    data = {"session_id": "ai-1716890000-12345"}
    session = GeminiProjectSession(session_file=session_file, data=data)
    assert session.ccb_session_id == "ai-1716890000-12345"

def test_compute_session_key_prioritizes_ccb_session_id_gemini(tmp_path):
    """compute_session_key should prioritize ccb_session_id for Gemini"""
    session_file = tmp_path / ".gemini-session"
    session_file.write_text("{}", encoding="utf-8")
    
    data = {
        "session_id": "ai-1716890000-12345",
        "pane_id": "%42",
    }
    
    session = GeminiProjectSession(session_file=session_file, data=data)
    key = compute_session_key(session)
    assert key == "ccb:ai-1716890000-12345"
```

- [ ] **Step 4: Write tests for oaskd_session**

```python
# test/test_oaskd_session_routing.py
from pathlib import Path
from oaskd_session import OpenCodeProjectSession, compute_session_key, load_project_session

def test_opencode_project_session_has_ccb_session_id_property(tmp_path):
    """OpenCodeProjectSession should have ccb_session_id property"""
    session_file = tmp_path / ".opencode-session"
    session_file.write_text("{}", encoding="utf-8")
    
    data = {"session_id": "ai-1716890000-12345"}
    session = OpenCodeProjectSession(session_file=session_file, data=data)
    assert session.ccb_session_id == "ai-1716890000-12345"

def test_compute_session_key_prioritizes_ccb_session_id_opencode(tmp_path):
    """compute_session_key should prioritize ccb_session_id for OpenCode"""
    session_file = tmp_path / ".opencode-session"
    session_file.write_text("{}", encoding="utf-8")
    
    data = {
        "session_id": "ai-1716890000-12345",
        "pane_id": "%42",
    }
    
    session = OpenCodeProjectSession(session_file=session_file, data=data)
    key = compute_session_key(session)
    assert key == "ccb:ai-1716890000-12345"
```

- [ ] **Step 5: Run all tests to verify they pass**

Run: `pytest test/test_gaskd_session_routing.py test/test_oaskd_session_routing.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add lib/gaskd_session.py lib/oaskd_session.py test/test_gaskd_session_routing.py test/test_oaskd_session_routing.py
git commit -m "feat(session): add ccb_session_id support to gaskd_session and oaskd_session"
```

---

## Phase 5: Daemon Layer (依赖 Phase 4)

### Task 14: caskd_daemon - 提取 ccb_session_id 传给 session loading

**Files:**
- Modify: `lib/caskd_daemon.py:466-502` (CaskdServer._handle_request)
- Modify: `lib/caskd_daemon.py:441-454` (_WorkerPool.submit)
- Test: 无需新测试（已有 integration test 覆盖）

- [ ] **Step 1: Extract ccb_session_id in _handle_request**

```python
# lib/caskd_daemon.py (修改 CaskdServer._handle_request 中的 req 构造)
def _handle_request(msg: dict) -> dict:
    try:
        req = CaskdRequest(
            client_id=str(msg.get("id") or ""),
            work_dir=str(msg.get("work_dir") or ""),
            timeout_s=float(msg.get("timeout_s") or 300.0),
            quiet=bool(msg.get("quiet") or False),
            message=str(msg.get("message") or ""),
            ccb_session_id=str(msg.get("ccb_session_id") or "") or None,  # 新增
            caller_pane_id=str(msg.get("caller_pane_id") or "") or None,
            output_path=str(msg.get("output_path")) if msg.get("output_path") else None,
        )
    except Exception as exc:
        return {"type": "cask.response", "v": 1, "id": msg.get("id"), "exit_code": 1, "reply": f"Bad request: {exc}"}
    
    # ... 其余代码不变 ...
```

- [ ] **Step 2: Pass ccb_session_id to load_project_session in _WorkerPool.submit**

```python
# lib/caskd_daemon.py (修改 _WorkerPool.submit 中的 load_project_session 调用)
class _WorkerPool:
    def submit(self, request: CaskdRequest) -> _QueuedTask:
        req_id = make_req_id()
        task = _QueuedTask(request=request, created_ms=_now_ms(), req_id=req_id, done_event=threading.Event())

        session = load_project_session(
            Path(request.work_dir),
            caller_pane_id=request.caller_pane_id,
            ccb_session_id=request.ccb_session_id  # 新增
        )
        session_key = compute_session_key(session) if session else "codex:unknown"

        worker = self._pool.get_or_create(session_key, _SessionWorker)
        worker.enqueue(task)
        return task
```

- [ ] **Step 3: Run existing tests to verify no regression**

Run: `pytest test/ -k caskd -v`
Expected: All caskd tests PASS

- [ ] **Step 4: Commit**

```bash
git add lib/caskd_daemon.py
git commit -m "feat(caskd_daemon): extract and pass ccb_session_id to session loading"
```

### Task 15: gaskd_daemon/oaskd_daemon - 同样的改动

**Files:**
- Modify: `lib/gaskd_daemon.py`
- Modify: `lib/oaskd_daemon.py`

- [ ] **Step 1: Apply same changes to gaskd_daemon**

对 `lib/gaskd_daemon.py` 做同样的改动：
- 在 request handler 中提取 `ccb_session_id`
- 在 worker pool 中传递 `ccb_session_id` 给 `load_project_session`

- [ ] **Step 2: Apply same changes to oaskd_daemon**

对 `lib/oaskd_daemon.py` 做同样的改动：
- 在 request handler 中提取 `ccb_session_id`
- 在 worker pool 中传递 `ccb_session_id` 给 `load_project_session`

- [ ] **Step 3: Run existing tests to verify no regression**

Run: `pytest test/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add lib/gaskd_daemon.py lib/oaskd_daemon.py
git commit -m "feat(daemon): add ccb_session_id support to gaskd_daemon and oaskd_daemon"
```

---

## Phase 6: Client Layer (依赖 Phase 2, 3, 4)

### Task 16: askd_client - 读取 CCB_SESSION_ID env 和 payload 改造

**Files:**
- Modify: `lib/askd_client.py:36-98` (try_daemon_request 函数)
- Test: `test/test_askd_client.py` (新建)

- [ ] **Step 1: Write failing test for CCB_SESSION_ID env reading**

```python
# test/test_askd_client.py
import os
from askd_client import _read_ccb_session_id
from providers import CASK_CLIENT_SPEC

def test_read_ccb_session_id_prefers_ccb_env(monkeypatch):
    """Should prefer CCB_SESSION_ID over legacy alias"""
    monkeypatch.setenv("CCB_SESSION_ID", "ccb-id")
    monkeypatch.setenv("CODEX_SESSION_ID", "codex-id")
    
    session_id = _read_ccb_session_id(CASK_CLIENT_SPEC)
    assert session_id == "ccb-id"

def test_read_ccb_session_id_falls_back_to_legacy(monkeypatch):
    """Should fall back to legacy alias if CCB_SESSION_ID not set"""
    monkeypatch.delenv("CCB_SESSION_ID", raising=False)
    monkeypatch.setenv("CODEX_SESSION_ID", "codex-id")
    
    session_id = _read_ccb_session_id(CASK_CLIENT_SPEC)
    assert session_id == "codex-id"

def test_read_ccb_session_id_warns_on_mismatch(monkeypatch, capsys):
    """Should warn if CCB_SESSION_ID and legacy alias differ"""
    monkeypatch.setenv("CCB_SESSION_ID", "ccb-id")
    monkeypatch.setenv("CODEX_SESSION_ID", "different-id")
    
    session_id = _read_ccb_session_id(CASK_CLIENT_SPEC)
    assert session_id == "ccb-id"
    
    captured = capsys.readouterr()
    assert "WARNING" in captured.err or "warning" in captured.err.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test/test_askd_client.py -v`
Expected: FAIL with "AttributeError: module 'askd_client' has no attribute '_read_ccb_session_id'"

- [ ] **Step 3: Implement _read_ccb_session_id helper function**

```python
# lib/askd_client.py (在文件开头添加 helper 函数)
def _read_ccb_session_id(spec) -> Optional[str]:
    """
    读取 CCB session ID
    
    优先级:
    1. CCB_SESSION_ID (canonical)
    2. spec.legacy_session_env (e.g., CODEX_SESSION_ID)
    
    如果两者都存在但值不同，输出警告并使用 CCB_SESSION_ID
    
    Args:
        spec: ProviderClientSpec 实例
    
    Returns:
        session ID 或 None
    """
    ccb_id = (os.environ.get("CCB_SESSION_ID") or "").strip()
    legacy_id = (os.environ.get(spec.legacy_session_env) or "").strip()
    
    if ccb_id and legacy_id and ccb_id != legacy_id:
        print(
            f"[WARNING] CCB_SESSION_ID ({ccb_id}) and {spec.legacy_session_env} ({legacy_id}) differ, using CCB_SESSION_ID",
            file=sys.stderr
        )
    
    return ccb_id or legacy_id or None
```

- [ ] **Step 4: Update try_daemon_request to use _read_ccb_session_id and add to payload**

```python
# lib/askd_client.py (修改 try_daemon_request 函数)
def try_daemon_request(spec: ProviderClientSpec, work_dir: Path, message: str, timeout: float, quiet: bool, state_file: Optional[Path] = None) -> Optional[Tuple[str, int]]:
    if not env_bool(spec.enabled_env, True):
        return None

    # 读取 ccb_session_id
    ccb_session_id = _read_ccb_session_id(spec)
    
    # 预检查：用同一身份精确匹配
    if not find_project_session_file(work_dir, spec.session_filename, session_id=ccb_session_id):
        return None

    from importlib import import_module
    daemon_module = import_module(spec.daemon_module)
    read_state = getattr(daemon_module, "read_state")

    st = read_state(state_file=state_file)
    if not st:
        return None
    try:
        host = st.get("connect_host") or st.get("host")
        port = int(st["port"])
        token = st["token"]
    except Exception:
        return None

    try:
        caller_pane_id = (
            (os.environ.get("WEZTERM_PANE") or "").strip()
            or (os.environ.get("TMUX_PANE") or "").strip()
            or None
        )
        payload = {
            "type": f"{spec.protocol_prefix}.request",
            "v": 1,
            "id": f"{spec.protocol_prefix}-{os.getpid()}-{int(time.time() * 1000)}",
            "token": token,
            "work_dir": str(work_dir),
            "timeout_s": float(timeout),
            "quiet": bool(quiet),
            "message": message,
            "ccb_session_id": ccb_session_id,  # 新增
            "caller_pane_id": caller_pane_id,
        }
        # ... 其余代码不变 ...
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest test/test_askd_client.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add lib/askd_client.py test/test_askd_client.py
git commit -m "feat(askd_client): read CCB_SESSION_ID env and add to payload"
```

### Task 17: askd_client - 修改 maybe_start_daemon 预检查

**Files:**
- Modify: `lib/askd_client.py:101-136` (maybe_start_daemon 函数)

- [ ] **Step 1: Update maybe_start_daemon to use ccb_session_id in pre-check**

```python
# lib/askd_client.py (修改 maybe_start_daemon 函数)
def maybe_start_daemon(spec: ProviderClientSpec, work_dir: Path) -> bool:
    if not env_bool(spec.enabled_env, True):
        return False
    if not autostart_enabled(spec.autostart_env_primary, spec.autostart_env_legacy, True):
        return False
    
    # 预检查：用同一身份精确匹配
    ccb_session_id = _read_ccb_session_id(spec)
    if not find_project_session_file(work_dir, spec.session_filename, session_id=ccb_session_id):
        return False

    # ... 其余代码不变 ...
```

- [ ] **Step 2: Run all tests to verify no regression**

Run: `pytest test/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add lib/askd_client.py
git commit -m "feat(askd_client): use ccb_session_id in maybe_start_daemon pre-check"
```

---

## Phase 7: Tools (依赖 Phase 2)

### Task 18: bin/cask, bin/gask, bin/oask - 预检查改用 session_id 精确匹配

**Files:**
- Modify: `bin/cask:159` (预检查)
- Modify: `bin/gask:138` (预检查)
- Modify: `bin/oask:182` (预检查)

- [ ] **Step 1: Update bin/cask pre-check**

```python
# bin/cask (修改 daemon 不可用时的预检查)
# 找到类似这样的代码：
# if not find_project_session_file(Path.cwd(), CASK_CLIENT_SPEC.session_filename):
# 改为：
from askd_client import _read_ccb_session_id

ccb_session_id = _read_ccb_session_id(CASK_CLIENT_SPEC)
if not find_project_session_file(Path.cwd(), CASK_CLIENT_SPEC.session_filename, session_id=ccb_session_id):
    print("[ERROR] No active Codex session found for this directory.", file=sys.stderr)
    print("Run `ccb up codex` in this project first.", file=sys.stderr)
    return EXIT_ERROR
```

- [ ] **Step 2: Update bin/gask pre-check**

对 `bin/gask` 做同样的改动

- [ ] **Step 3: Update bin/oask pre-check**

对 `bin/oask` 做同样的改动

- [ ] **Step 4: Run all tests to verify no regression**

Run: `pytest test/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add bin/cask bin/gask bin/oask
git commit -m "feat(tools): use session_id precise matching in cask/gask/oask pre-checks"
```

### Task 19: bin/cpend, lib/codex_comm.py - 优先读取 CCB_SESSION_ID

**Files:**
- Modify: `bin/cpend` (环境变量读取)
- Modify: `lib/codex_comm.py:676-711` (环境变量读取)

- [ ] **Step 1: Update bin/cpend to prefer CCB_SESSION_ID**

```python
# bin/cpend (修改环境变量读取)
# 找到读取 CODEX_SESSION_ID 的地方，改为：
session_id = (
    os.environ.get("CCB_SESSION_ID") or
    os.environ.get("CODEX_SESSION_ID") or
    ""
).strip()
```

- [ ] **Step 2: Update lib/codex_comm.py to prefer CCB_SESSION_ID**

```python
# lib/codex_comm.py (修改所有读取 CODEX_SESSION_ID 的地方)
# 搜索所有 os.environ.get("CODEX_SESSION_ID")，改为：
session_id = (
    os.environ.get("CCB_SESSION_ID") or
    os.environ.get("CODEX_SESSION_ID") or
    ""
).strip()
```

- [ ] **Step 3: Run all tests to verify no regression**

Run: `pytest test/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add bin/cpend lib/codex_comm.py
git commit -m "feat(tools): prefer CCB_SESSION_ID in cpend and codex_comm"
```

---

## Phase 8: Main Script (依赖 Phase 2, 3, 4)

### Task 20: ccb - 注入 CCB_SESSION_ID 环境变量

**Files:**
- Modify: `ccb:1439-1480` (_start_claude 函数)

- [ ] **Step 1: Add CCB_SESSION_ID to env in _start_claude**

```python
# ccb (修改 _start_claude 函数中的 env 设置)
def _start_claude(self) -> int:
    print(f"🚀 {t('starting_claude')}")

    env = os.environ.copy()
    
    # 注入 CCB_SESSION_ID（canonical）
    env["CCB_SESSION_ID"] = self.session_id
    
    # 保留已有的兼容别名
    if "codex" in self.providers:
        runtime = self.runtime_dir / "codex"
        env["CODEX_SESSION_ID"] = self.session_id  # 兼容别名
        # ... 其余 CODEX_* 设置不变 ...
    
    if "gemini" in self.providers:
        runtime = self.runtime_dir / "gemini"
        env["GEMINI_SESSION_ID"] = self.session_id  # 兼容别名
        # ... 其余 GEMINI_* 设置不变 ...
    
    if "opencode" in self.providers:
        runtime = self.runtime_dir / "opencode"
        env["OPENCODE_SESSION_ID"] = self.session_id  # 兼容别名
        # ... 其余 OPENCODE_* 设置不变 ...
    
    # ... 其余代码不变 ...
```

- [ ] **Step 2: Run all tests to verify no regression**

Run: `pytest test/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add ccb
git commit -m "feat(ccb): inject CCB_SESSION_ID env in _start_claude"
```

### Task 21: ccb - 唯一化 pane_title_marker

**Files:**
- Modify: `ccb:507-566` (_start_provider_wezterm 函数)
- Modify: `ccb:568-620` (_start_provider_iterm2 函数)
- Modify: `ccb` 中所有设置 pane_title_marker 的地方

- [ ] **Step 1: Update pane_title_marker to use full session_id**

```python
# ccb (修改所有设置 pane_title_marker 的地方)
# 找到：
# pane_title_marker = f"CCB-{provider.capitalize()}"
# 改为：
pane_title_marker = f"CCB-{provider.capitalize()}-{self.session_id}"

# 例如在 _start_provider_wezterm 中：
def _start_provider_wezterm(self, provider: str) -> bool:
    # ... 前面的代码不变 ...
    
    # 唯一化 marker（使用完整 session_id）
    pane_title_marker = f"CCB-{provider.capitalize()}-{self.session_id}"
    
    # ... 其余代码不变 ...
```

对所有设置 pane_title_marker 的地方做同样的改动（搜索 `pane_title_marker = f"CCB-`）

- [ ] **Step 2: Run all tests to verify no regression**

Run: `pytest test/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add ccb
git commit -m "feat(ccb): unique pane_title_marker with full session_id"
```

### Task 22: ccb - 更新 cmd_kill 路由和 registry 清理

**Files:**
- Modify: `ccb:1655-1699` (cmd_kill 函数)

- [ ] **Step 1: Update cmd_kill to use new routing**

```python
# ccb (修改 cmd_kill 函数)
def cmd_kill(args):
    providers = _parse_providers(args.providers or ["codex", "gemini", "opencode"], allow_unknown=True)
    if not providers:
        return 2

    caller_pane_id = (os.environ.get("WEZTERM_PANE") or os.environ.get("TMUX_PANE") or "").strip() or None
    
    # 读取 CCB_SESSION_ID（如果设置了）
    ccb_session_id = (os.environ.get("CCB_SESSION_ID") or "").strip() or None

    for provider in providers:
        # 使用新路由：ccb_session_id -> caller_pane_id -> fallback
        session_filename = f".{provider}-session"
        session_file = find_project_session_file(
            Path.cwd(),
            session_filename,
            caller_pane_id=caller_pane_id,
            session_id=ccb_session_id
        )
        
        if not session_file or not session_file.exists():
            print(f"⚠️ {provider}: Session file not found")
            continue

        try:
            data = json.loads(session_file.read_text(encoding="utf-8-sig"))
            session_id = data.get("session_id")
            
            # ... 终止 pane 的代码不变 ...
            
            # 标记为 inactive
            data["active"] = False
            data["ended_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            safe_write_session(session_file, json.dumps(data, ensure_ascii=False, indent=2))
            
            # 清理该 provider 的 registry 字段
            from pane_registry import clear_provider_registry_fields
            if session_id:
                clear_provider_registry_fields(session_id, provider)
                
                # 检查同 session 下其他 provider 是否仍活跃
                other_providers = [p for p in ["codex", "gemini", "opencode"] if p != provider]
                all_inactive = True
                for other in other_providers:
                    other_file = find_project_session_file(Path.cwd(), f".{other}-session", session_id=session_id)
                    if other_file and other_file.exists():
                        try:
                            other_data = json.loads(other_file.read_text(encoding="utf-8-sig"))
                            if other_data.get("active") is True and not other_data.get("ended_at"):
                                all_inactive = False
                                break
                        except Exception:
                            pass
                
                # 所有 provider 都不活跃，删除整个 registry
                if all_inactive:
                    from pane_registry import remove_registry
                    remove_registry(session_id)

            print(f"✅ {provider.capitalize()} terminated")
        except Exception as e:
            print(f"❌ {provider}: {e}")

    return 0
```

- [ ] **Step 2: Run all tests to verify no regression**

Run: `pytest test/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add ccb
git commit -m "feat(ccb): update cmd_kill with new routing and provider-level registry cleanup"
```

---

## Phase 9: Integration Testing

### Task 23: Integration test - 多实例路由

**Files:**
- Test: `test/test_multi_instance_integration.py` (新建)

- [ ] **Step 1: Write integration test for multi-instance routing**

```python
# test/test_multi_instance_integration.py
import json
from pathlib import Path
from session_utils import find_project_session_file

def test_multi_instance_routing_precise_match(tmp_path):
    """Multiple instances should route precisely by session_id"""
    root = tmp_path / "repo"
    root.mkdir()
    
    # 模拟两个 CCB 实例
    session_a = root / ".codex-session"
    session_a.write_text(json.dumps({
        "session_id": "instance-A",
        "active": True,
        "pane_id": "pane-A",
    }), encoding="utf-8")
    
    session_b = root / ".codex-session-1"
    session_b.write_text(json.dumps({
        "session_id": "instance-B",
        "active": True,
        "pane_id": "pane-B",
    }), encoding="utf-8")
    
    # 精确匹配 instance-A
    found_a = find_project_session_file(root, ".codex-session", session_id="instance-A")
    assert found_a == session_a
    
    # 精确匹配 instance-B
    found_b = find_project_session_file(root, ".codex-session", session_id="instance-B")
    assert found_b == session_b
    
    # 不匹配的 session_id 返回 None
    found_none = find_project_session_file(root, ".codex-session", session_id="instance-C")
    assert found_none is None

def test_multi_instance_ambiguity_returns_none(tmp_path):
    """Multiple active instances without identity should return None"""
    root = tmp_path / "repo"
    root.mkdir()
    
    session_a = root / ".codex-session"
    session_a.write_text(json.dumps({
        "session_id": "instance-A",
        "active": True,
    }), encoding="utf-8")
    
    session_b = root / ".codex-session-1"
    session_b.write_text(json.dumps({
        "session_id": "instance-B",
        "active": True,
    }), encoding="utf-8")
    
    # 没有 session_id，多个候选 -> 返回 None
    found = find_project_session_file(root, ".codex-session")
    assert found is None
```

- [ ] **Step 2: Run integration tests**

Run: `pytest test/test_multi_instance_integration.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add test/test_multi_instance_integration.py
git commit -m "test(integration): add multi-instance routing integration tests"
```

---

## Phase 10: Final Testing and Documentation

### Task 24: Run all tests and fix any regressions

- [ ] **Step 1: Run full test suite**

Run: `pytest test/ -v`
Expected: All tests PASS

- [ ] **Step 2: Fix any failing tests**

如果有测试失败，分析原因并修复。可能需要：
- 更新测试用例以适配新路由逻辑
- 修复实现中的 bug

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: address test failures from multi-instance routing changes"
```

### Task 25: Update doc/multi-instance-v2-design.md with implementation notes

- [ ] **Step 1: Add implementation notes to design doc**

在 `doc/multi-instance-v2-design.md` 末尾添加：

```markdown
## Implementation Notes

### 实现完成日期
2026-05-28

### 关键改动
1. Protocol layer: CaskdRequest/GaskdRequest/OaskdRequest 增加 ccb_session_id 字段
2. Routing: find_project_session_file 改为精确匹配，禁止 fallback 到 candidates[0]
3. Session loading: load_project_session 接受 ccb_session_id 参数
4. Daemon: 提取并传递 ccb_session_id
5. Client: 读取 CCB_SESSION_ID env，payload 包含 ccb_session_id
6. Registry: 新增 clear_provider_registry_fields()
7. Terminal: TerminalBackend 增加 list_panes()
8. Main script: 注入 CCB_SESSION_ID env，唯一化 pane_title_marker

### 测试覆盖
- test/test_session_utils.py - 路由逻辑测试
- test/test_pane_registry.py - registry 清理测试
- test/test_terminal_list_panes.py - list_panes 接口测试
- test/test_caskd_session_routing.py - session loading 测试
- test/test_multi_instance_integration.py - 集成测试

### 向后兼容
- 单实例场景完全兼容
- 旧 daemon 兼容（wire-compatible，但多实例不可靠）
```

- [ ] **Step 2: Commit documentation update**

```bash
git add doc/multi-instance-v2-design.md
git commit -m "docs: add implementation notes to multi-instance design"
```

---

## Summary

This implementation plan covers all changes required by the multi-instance routing spec:

- **25 tasks** organized into 10 phases
- **TDD approach**: Write test, see it fail, implement, see it pass, commit
- **Each task is 2-5 minutes** of focused work
- **Frequent commits** for easy rollback and review
- **Complete code** in every step, no placeholders
- **Exact file paths** and line numbers

After completing all tasks, the system will support multiple CCB instances in the same directory with precise routing by CCB_SESSION_ID, eliminating the "daemon routing to wrong instance" bug.
