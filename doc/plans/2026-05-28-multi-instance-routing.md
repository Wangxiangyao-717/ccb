# CCB 同目录多实例路由 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 CCB 同目录多实例支持，通过显式 CCB_SESSION_ID 主路由解决 daemon 路由错乱问题

**Architecture:** 引入 CCB_SESSION_ID 作为规范身份标识，修改路由逻辑为精确匹配（禁止 fallback 到 candidates[0]），支持 caller_pane_id fallback 和两阶段扫描解决 ambiguity

**Tech Stack:** Python 3.11+, pytest, WezTerm/tmux/iTerm2 terminal backends

---

## File Structure

### 新增文件
- 无

### 修改文件
- `lib/ccb_protocol.py` - CaskdRequest 增加 ccb_session_id 字段
- `lib/gaskd_protocol.py` - GaskdRequest 增加 ccb_session_id 字段
- `lib/oaskd_protocol.py` - OaskdRequest 增加 ccb_session_id 字段
- `lib/providers.py` - ProviderClientSpec 增加 legacy_session_env 字段
- `lib/session_utils.py` - 修改 find_project_session_file 路由逻辑，新增 list_session_candidates()
- `lib/terminal.py` - TerminalBackend 增加 list_panes() 抽象方法，各 backend 实现
- `lib/caskd_session.py` - 增加 ccb_session_id property，修改 compute_session_key()，修改 load_project_session()
- `lib/gaskd_session.py` - 同上
- `lib/oaskd_session.py` - 同上
- `lib/caskd_daemon.py` - 提取 ccb_session_id 传给 session loading
- `lib/gaskd_daemon.py` - 同上
- `lib/oaskd_daemon.py` - 同上
- `lib/askd_client.py` - 读取 CCB_SESSION_ID env，payload 增加 ccb_session_id，修改预检查
- `lib/pane_registry.py` - 新增 clear_provider_registry_fields()
- `bin/cask` - 预检查改用 session_id 精确匹配
- `bin/gask` - 同上
- `bin/oask` - 同上
- `bin/cpend` - 优先读取 CCB_SESSION_ID
- `lib/codex_comm.py` - 优先读取 CCB_SESSION_ID
- `ccb` - 注入 CCB_SESSION_ID env，唯一化 pane_title_marker，更新 cmd_kill 路由和 registry 清理

### 测试文件
- `test/test_session_utils.py` - 已有，需要扩展
- `test/test_multi_instance_routing.py` - 新增，测试多实例路由
- `test/test_pane_registry.py` - 新增，测试 clear_provider_registry_fields
- `test/test_terminal_list_panes.py` - 新增，测试 list_panes

---

## Phase 1: Foundation (无依赖)

### Task 1: Protocol Layer - CaskdRequest 增加 ccb_session_id

**Files:**
- Modify: `lib/ccb_protocol.py:90-98`
- Test: `test/test_multi_instance_routing.py` (新建)

- [ ] **Step 1: Write failing test for CaskdRequest with ccb_session_id**

```python
# test/test_multi_instance_routing.py
from ccb_protocol import CaskdRequest

def test_caskd_request_accepts_ccb_session_id():
    """CaskdRequest should accept ccb_session_id as optional field"""
    req = CaskdRequest(
        client_id="test-client",
        work_dir="/tmp/project",
        timeout_s=300.0,
        quiet=False,
        message="test message",
        ccb_session_id="ai-1716890000-12345",
        caller_pane_id="pane-1"
    )
    assert req.ccb_session_id == "ai-1716890000-12345"
    assert req.caller_pane_id == "pane-1"

def test_caskd_request_ccb_session_id_optional():
    """ccb_session_id should be optional (backward compatibility)"""
    req = CaskdRequest(
        client_id="test-client",
        work_dir="/tmp/project",
        timeout_s=300.0,
        quiet=False,
        message="test message"
    )
    assert req.ccb_session_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_multi_instance_routing.py::test_caskd_request_accepts_ccb_session_id -v`
Expected: FAIL with "TypeError: __init__() got an unexpected keyword argument 'ccb_session_id'"

- [ ] **Step 3: Implement ccb_session_id field in CaskdRequest**

```python
# lib/ccb_protocol.py (修改 CaskdRequest dataclass)
@dataclass(frozen=True)
class CaskdRequest:
    client_id: str
    work_dir: str
    timeout_s: float
    quiet: bool
    message: str
    ccb_session_id: str | None = None  # 新增
    caller_pane_id: str | None = None
    output_path: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test/test_multi_instance_routing.py::test_caskd_request_accepts_ccb_session_id -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/ccb_protocol.py test/test_multi_instance_routing.py
git commit -m "feat(protocol): add ccb_session_id to CaskdRequest"
```

### Task 2: Protocol Layer - GaskdRequest/OaskdRequest 增加 ccb_session_id

**Files:**
- Modify: `lib/gaskd_protocol.py:66-73`
- Modify: `lib/oaskd_protocol.py:26-33`
- Test: `test/test_multi_instance_routing.py` (扩展)

- [ ] **Step 1: Write failing test for GaskdRequest with ccb_session_id**

```python
# test/test_multi_instance_routing.py (追加)
from gaskd_protocol import GaskdRequest
from oaskd_protocol import OaskdRequest

def test_gaskd_request_accepts_ccb_session_id():
    """GaskdRequest should accept ccb_session_id (no caller_pane_id)"""
    req = GaskdRequest(
        client_id="test-client",
        work_dir="/tmp/project",
        timeout_s=300.0,
        quiet=False,
        message="test message",
        ccb_session_id="ai-1716890000-12345"
    )
    assert req.ccb_session_id == "ai-1716890000-12345"
    # GaskdRequest should NOT have caller_pane_id
    assert not hasattr(req, 'caller_pane_id')

def test_oaskd_request_accepts_ccb_session_id():
    """OaskdRequest should accept ccb_session_id (no caller_pane_id)"""
    req = OaskdRequest(
        client_id="test-client",
        work_dir="/tmp/project",
        timeout_s=300.0,
        quiet=False,
        message="test message",
        ccb_session_id="ai-1716890000-12345"
    )
    assert req.ccb_session_id == "ai-1716890000-12345"
    assert not hasattr(req, 'caller_pane_id')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_multi_instance_routing.py::test_gaskd_request_accepts_ccb_session_id -v`
Expected: FAIL with "TypeError: __init__() got an unexpected keyword argument 'ccb_session_id'"

- [ ] **Step 3: Implement ccb_session_id in GaskdRequest**

```python
# lib/gaskd_protocol.py (修改 GaskdRequest dataclass)
@dataclass(frozen=True)
class GaskdRequest:
    client_id: str
    work_dir: str
    timeout_s: float
    quiet: bool
    message: str
    ccb_session_id: str | None = None  # 新增（仅此一个字段，不加 caller_pane_id）
    output_path: str | None = None
```

- [ ] **Step 4: Implement ccb_session_id in OaskdRequest**

```python
# lib/oaskd_protocol.py (修改 OaskdRequest dataclass)
@dataclass(frozen=True)
class OaskdRequest:
    client_id: str
    work_dir: str
    timeout_s: float
    quiet: bool
    message: str
    ccb_session_id: str | None = None  # 新增（仅此一个字段，不加 caller_pane_id）
    output_path: str | None = None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest test/test_multi_instance_routing.py::test_gaskd_request_accepts_ccb_session_id test/test_multi_instance_routing.py::test_oaskd_request_accepts_ccb_session_id -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add lib/gaskd_protocol.py lib/oaskd_protocol.py test/test_multi_instance_routing.py
git commit -m "feat(protocol): add ccb_session_id to GaskdRequest and OaskdRequest"
```

### Task 3: ProviderClientSpec - 增加 legacy_session_env 字段

**Files:**
- Modify: `lib/providers.py:16-25` (ProviderClientSpec dataclass)
- Modify: `lib/providers.py:58-91` (CASK_CLIENT_SPEC, GASK_CLIENT_SPEC, OASK_CLIENT_SPEC)
- Test: `test/test_multi_instance_routing.py` (扩展)

- [ ] **Step 1: Write failing test for legacy_session_env**

```python
# test/test_multi_instance_routing.py (追加)
from providers import CASK_CLIENT_SPEC, GASK_CLIENT_SPEC, OASK_CLIENT_SPEC

def test_provider_client_spec_has_legacy_session_env():
    """Each provider spec should have legacy_session_env field"""
    assert CASK_CLIENT_SPEC.legacy_session_env == "CODEX_SESSION_ID"
    assert GASK_CLIENT_SPEC.legacy_session_env == "GEMINI_SESSION_ID"
    assert OASK_CLIENT_SPEC.legacy_session_env == "OPENCODE_SESSION_ID"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_multi_instance_routing.py::test_provider_client_spec_has_legacy_session_env -v`
Expected: FAIL with "AttributeError: 'ProviderClientSpec' object has no attribute 'legacy_session_env'"

- [ ] **Step 3: Add legacy_session_env to ProviderClientSpec**

```python
# lib/providers.py (修改 ProviderClientSpec dataclass，在 daemon_module 后面添加)
@dataclass
class ProviderClientSpec:
    protocol_prefix: str
    enabled_env: str
    autostart_env_primary: str
    autostart_env_legacy: str
    state_file_env: str
    session_filename: str
    daemon_bin_name: str
    daemon_module: str
    legacy_session_env: str  # 新增
```

- [ ] **Step 4: Add legacy_session_env to each spec instance**

```python
# lib/providers.py (修改 CASK_CLIENT_SPEC)
CASK_CLIENT_SPEC = ProviderClientSpec(
    protocol_prefix="cask",
    enabled_env="CCB_CASKD",
    autostart_env_primary="CCB_CASKD_AUTOSTART",
    autostart_env_legacy="CCB_AUTO_CASKD",
    state_file_env="CCB_CASKD_STATE_FILE",
    session_filename=".codex-session",
    daemon_bin_name="caskd",
    daemon_module="caskd_daemon",
    legacy_session_env="CODEX_SESSION_ID",  # 新增
)

# 修改 GASK_CLIENT_SPEC
GASK_CLIENT_SPEC = ProviderClientSpec(
    protocol_prefix="gask",
    enabled_env="CCB_GASKD",
    autostart_env_primary="CCB_GASKD_AUTOSTART",
    autostart_env_legacy="CCB_AUTO_GASKD",
    state_file_env="CCB_GASKD_STATE_FILE",
    session_filename=".gemini-session",
    daemon_bin_name="gaskd",
    daemon_module="gaskd_daemon",
    legacy_session_env="GEMINI_SESSION_ID",  # 新增
)

# 修改 OASK_CLIENT_SPEC
OASK_CLIENT_SPEC = ProviderClientSpec(
    protocol_prefix="oask",
    enabled_env="CCB_OASKD",
    autostart_env_primary="CCB_OASKD_AUTOSTART",
    autostart_env_legacy="CCB_AUTO_OASKD",
    state_file_env="CCB_OASKD_STATE_FILE",
    session_filename=".opencode-session",
    daemon_bin_name="oaskd",
    daemon_module="oaskd_daemon",
    legacy_session_env="OPENCODE_SESSION_ID",  # 新增
)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest test/test_multi_instance_routing.py::test_provider_client_spec_has_legacy_session_env -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add lib/providers.py test/test_multi_instance_routing.py
git commit -m "feat(providers): add legacy_session_env to ProviderClientSpec"
```

### Task 4: pane_registry - 新增 clear_provider_registry_fields()

**Files:**
- Modify: `lib/pane_registry.py` (在 remove_registry 后面添加新函数)
- Test: `test/test_pane_registry.py` (新建)

- [ ] **Step 1: Write failing test for clear_provider_registry_fields**

```python
# test/test_pane_registry.py
import json
from pathlib import Path
from pane_registry import clear_provider_registry_fields, upsert_registry, load_registry_by_session_id

def test_clear_provider_registry_fields_removes_only_provider_keys(tmp_path, monkeypatch):
    """Should remove only provider-specific fields, keep other providers and claude_pane_id"""
    monkeypatch.setattr("pane_registry._registry_dir", lambda: tmp_path)
    
    # 创建包含多个 provider 的 registry
    upsert_registry({
        "ccb_session_id": "test-session",
        "claude_pane_id": "claude-pane-1",
        "codex_pane_id": "codex-pane-1",
        "codex_runtime_dir": "/tmp/codex",
        "gemini_pane_id": "gemini-pane-1",
        "gemini_runtime_dir": "/tmp/gemini",
    })
    
    # 清理 codex 字段
    result = clear_provider_registry_fields("test-session", "codex")
    assert result is True
    
    # 验证 codex 字段被清理，其他保留
    data = load_registry_by_session_id("test-session")
    assert data is not None
    assert "codex_pane_id" not in data
    assert "codex_runtime_dir" not in data
    assert data["claude_pane_id"] == "claude-pane-1"
    assert data["gemini_pane_id"] == "gemini-pane-1"
    assert data["gemini_runtime_dir"] == "/tmp/gemini"

def test_clear_provider_registry_fields_nonexistent_session(tmp_path, monkeypatch):
    """Should return False for nonexistent session"""
    monkeypatch.setattr("pane_registry._registry_dir", lambda: tmp_path)
    result = clear_provider_registry_fields("nonexistent", "codex")
    assert result is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_pane_registry.py::test_clear_provider_registry_fields_removes_only_provider_keys -v`
Expected: FAIL with "ImportError: cannot import name 'clear_provider_registry_fields'"

- [ ] **Step 3: Implement clear_provider_registry_fields**

```python
# lib/pane_registry.py (在 remove_registry 函数后面添加)
def clear_provider_registry_fields(session_id: str, provider: str) -> bool:
    """
    清理 registry 文件中指定 provider 的字段，保留其他 provider 和 claude_pane_id
    
    Args:
        session_id: CCB session ID
        provider: provider 名称 ("codex", "gemini", "opencode")
    
    Returns:
        True if fields were cleared, False if session not found or error
    
    示例:
        clear_provider_registry_fields("ai-123", "codex")
        # 会删除 codex_pane_id, codex_runtime_dir, codex_input_fifo 等
        # 保留 claude_pane_id, gemini_*, opencode_* 等
    """
    if not session_id or not provider:
        return False
    
    path = registry_path_for_session(str(session_id))
    if not path.exists():
        return False
    
    data = _load_registry_file(path)
    if not data:
        return False
    
    # 找出所有 {provider}_ 开头的字段
    prefix = f"{provider}_"
    keys_to_remove = [k for k in data.keys() if k.startswith(prefix)]
    
    if not keys_to_remove:
        return True  # 没有要清理的字段，视为成功
    
    for key in keys_to_remove:
        del data[key]
    
    data["updated_at"] = int(time.time())
    
    try:
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))
        return True
    except Exception as exc:
        _debug(f"Failed to clear provider fields {path}: {exc}")
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test/test_pane_registry.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add lib/pane_registry.py test/test_pane_registry.py
git commit -m "feat(registry): add clear_provider_registry_fields()"
```

---

## Phase 2: Core Routing (依赖 Phase 1)

### Task 5: session_utils - 修改 find_project_session_file 路由逻辑

**Files:**
- Modify: `lib/session_utils.py:185-208`
- Test: `test/test_session_utils.py` (扩展)

- [ ] **Step 1: Write failing test for precise session_id matching**

```python
# test/test_session_utils.py (追加)
def test_find_project_session_file_precise_match_by_session_id(tmp_path: Path) -> None:
    """Should precisely match session file by session_id, not fallback to candidates[0]"""
    root = tmp_path / "repo"
    root.mkdir()
    
    # 创建两个 session 文件
    session0 = root / ".codex-session"
    session0.write_text(json.dumps({"session_id": "old-session"}), encoding="utf-8")
    
    session1 = root / ".codex-session-1"
    session1.write_text(json.dumps({"session_id": "new-session"}), encoding="utf-8")
    
    # 用 session_id 精确匹配
    found = find_project_session_file(root, ".codex-session", session_id="new-session")
    assert found == session1, f"Expected session1 but got {found}"

def test_find_project_session_file_no_fallback_on_mismatch(tmp_path: Path) -> None:
    """Should return None when session_id doesn't match, not fallback to candidates[0]"""
    root = tmp_path / "repo"
    root.mkdir()
    
    session0 = root / ".codex-session"
    session0.write_text(json.dumps({"session_id": "session-A"}), encoding="utf-8")
    
    session1 = root / ".codex-session-1"
    session1.write_text(json.dumps({"session_id": "session-B"}), encoding="utf-8")
    
    # session_id 不匹配任何文件
    found = find_project_session_file(root, ".codex-session", session_id="session-C")
    assert found is None, "Should return None, not fallback to candidates[0]"

def test_find_project_session_file_ambiguity_returns_none(tmp_path: Path) -> None:
    """Should return None when multiple active candidates exist without identity"""
    root = tmp_path / "repo"
    root.mkdir()
    
    session0 = root / ".codex-session"
    session0.write_text(json.dumps({"session_id": "s1", "active": True}), encoding="utf-8")
    
    session1 = root / ".codex-session-1"
    session1.write_text(json.dumps({"session_id": "s2", "active": True}), encoding="utf-8")
    
    # 没有 session_id 也没有 caller_pane_id，多个候选
    found = find_project_session_file(root, ".codex-session")
    assert found is None, "Should return None for ambiguity, not pick candidates[0]"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test/test_session_utils.py::test_find_project_session_file_precise_match_by_session_id test/test_session_utils.py::test_find_project_session_file_no_fallback_on_mismatch test/test_session_utils.py::test_find_project_session_file_ambiguity_returns_none -v`
Expected: All three tests FAIL (current implementation falls back to candidates[0])

- [ ] **Step 3: Rewrite find_project_session_file with new routing logic**

```python
# lib/session_utils.py (完全重写 find_project_session_file 函数)
def find_project_session_file(work_dir: Path, session_filename: str, *, caller_pane_id: str | None = None, session_id: str | None = None) -> Optional[Path]:
    """
    查找 session 文件，使用精确路由（禁止 fallback 到 candidates[0]）
    
    路由优先级:
    1. session_id 精确匹配
    2. caller_pane_id -> registry -> session_id 匹配
    3. 单候选（active=true && 无 ended_at）
    4. 多候选 -> 返回 None（上层做阶段 2 liveness probe）
    
    返回值:
    - Path: 唯一确定的 session 文件
    - None: 无候选或歧义（上层需自行处理）
    """
    expected_session_id = str(session_id or "").strip()
    
    # 如果没有 session_id，尝试通过 caller_pane_id 从 registry 查找
    if not expected_session_id:
        pane_id = str(caller_pane_id or "").strip()
        if pane_id:
            try:
                record = load_registry_by_claude_pane(pane_id)
            except Exception:
                record = None
            if isinstance(record, dict):
                # 验证 work_dir 一致性（防止跨项目误路由）
                registry_work_dir = _normalize_path_for_match(str(record.get("work_dir_norm") or record.get("work_dir") or ""))
                request_work_dir = _normalize_path_for_match(str(Path(work_dir).resolve()))
                if registry_work_dir and request_work_dir and registry_work_dir != request_work_dir:
                    record = None  # work_dir 不一致，视为 miss
                else:
                    expected_session_id = str(record.get("ccb_session_id") or "").strip()
    
    # 从 cwd 向父目录逐级查找
    current = Path(work_dir).resolve()
    while True:
        candidates = list(_iter_session_file_candidates(current, session_filename))
        
        # 过滤活跃候选（active=true && 无 ended_at）
        active_candidates = []
        for candidate in candidates:
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if data.get("active") is False or data.get("ended_at"):
                    continue
                active_candidates.append((candidate, data))
            except Exception:
                continue
        
        if expected_session_id:
            # 精确匹配 session_id
            for candidate, data in active_candidates:
                file_session_id = str(data.get("session_id") or "").strip()
                if file_session_id == expected_session_id:
                    return candidate
            # 精确匹配失败，返回 None（不 fallback）
            return None
        
        # 没有 session_id，按候选数量决定
        if len(active_candidates) == 0:
            # 当前目录无候选，继续向父目录查找
            if current == current.parent:
                return None
            current = current.parent
            continue
        elif len(active_candidates) == 1:
            # 单候选，直接返回
            return active_candidates[0][0]
        else:
            # 多候选，返回 None（上层做阶段 2）
            return None
```

- [ ] **Step 4: Run all session_utils tests to verify they pass**

Run: `pytest test/test_session_utils.py -v`
Expected: All tests PASS (包括原有的父目录查找测试)

- [ ] **Step 5: Commit**

```bash
git add lib/session_utils.py test/test_session_utils.py
git commit -m "feat(routing): implement precise session_id routing (no fallback)"
```

### Task 6: session_utils - 新增 list_session_candidates()

**Files:**
- Modify: `lib/session_utils.py` (在 find_project_session_file 后面添加)
- Test: `test/test_session_utils.py` (扩展)

- [ ] **Step 1: Write failing test for list_session_candidates**

```python
# test/test_session_utils.py (追加)
from session_utils import list_session_candidates

def test_list_session_candidates_returns_active_only(tmp_path: Path) -> None:
    """Should return only active candidates (active=true && no ended_at)"""
    root = tmp_path / "repo"
    root.mkdir()
    
    # 活跃候选
    session0 = root / ".codex-session"
    session0.write_text(json.dumps({"session_id": "s1", "active": True}), encoding="utf-8")
    
    # 非活跃（active=false）
    session1 = root / ".codex-session-1"
    session1.write_text(json.dumps({"session_id": "s2", "active": False}), encoding="utf-8")
    
    # 非活跃（有 ended_at）
    session2 = root / ".codex-session-2"
    session2.write_text(json.dumps({"session_id": "s3", "active": True, "ended_at": "2026-05-28"}), encoding="utf-8")
    
    candidates = list_session_candidates(root, ".codex-session")
    assert len(candidates) == 1
    assert candidates[0] == session0

def test_list_session_candidates_walks_upwards(tmp_path: Path) -> None:
    """Should search from cwd up to parent directories"""
    root = tmp_path / "repo"
    leaf = root / "a" / "b"
    leaf.mkdir(parents=True)
    
    session = root / ".codex-session"
    session.write_text(json.dumps({"session_id": "s1", "active": True}), encoding="utf-8")
    
    candidates = list_session_candidates(leaf, ".codex-session")
    assert len(candidates) == 1
    assert candidates[0] == session

def test_list_session_candidates_sorted_by_proximity(tmp_path: Path) -> None:
    """Should sort candidates by directory proximity (nearest first)"""
    root = tmp_path / "repo"
    sub = root / "sub"
    sub.mkdir()
    
    session_root = root / ".codex-session"
    session_root.write_text(json.dumps({"session_id": "s1", "active": True}), encoding="utf-8")
    
    session_sub = sub / ".codex-session-1"
    session_sub.write_text(json.dumps({"session_id": "s2", "active": True}), encoding="utf-8")
    
    candidates = list_session_candidates(sub, ".codex-session")
    assert len(candidates) == 2
    # sub 目录的候选应该排在前面
    assert candidates[0] == session_sub
    assert candidates[1] == session_root
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test/test_session_utils.py::test_list_session_candidates_returns_active_only -v`
Expected: FAIL with "ImportError: cannot import name 'list_session_candidates'"

- [ ] **Step 3: Implement list_session_candidates**

```python
# lib/session_utils.py (在 find_project_session_file 后面添加)
def list_session_candidates(work_dir: Path, session_filename: str) -> list[Path]:
    """
    返回当前目录及所有父目录中的活跃候选 session 文件列表
    
    仅做轻量过滤：active=true && 无 ended_at
    按目录从近到远排序，同目录内按编号排序
    
    用于上层 load_project_session() 做阶段 2 liveness probe
    
    Args:
        work_dir: 工作目录（从该目录开始向上查找）
        session_filename: session 文件名（如 ".codex-session"）
    
    Returns:
        活跃候选文件列表（按距离排序，近 -> 远）
    """
    all_candidates: list[tuple[int, Path]] = []  # (distance, path)
    current = Path(work_dir).resolve()
    distance = 0
    
    while True:
        candidates = list(_iter_session_file_candidates(current, session_filename))
        
        for candidate in candidates:
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if data.get("active") is False or data.get("ended_at"):
                    continue
                all_candidates.append((distance, candidate))
            except Exception:
                continue
        
        if current == current.parent:
            break
        current = current.parent
        distance += 1
    
    # 按 distance 排序（近 -> 远），同 distance 内保持 _iter 的顺序（按编号）
    all_candidates.sort(key=lambda x: x[0])
    return [path for _, path in all_candidates]
```

- [ ] **Step 4: Run all session_utils tests to verify they pass**

Run: `pytest test/test_session_utils.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add lib/session_utils.py test/test_session_utils.py
git commit -m "feat(session_utils): add list_session_candidates() for phase 2 probe"
```

---

## Phase 3: Terminal Backend (依赖 Phase 1)

### Task 7: terminal.py - TerminalBackend 增加 list_panes() 抽象方法

**Files:**
- Modify: `lib/terminal.py:205-216` (TerminalBackend 类)
- Test: `test/test_terminal_list_panes.py` (新建)

- [ ] **Step 1: Write failing test for list_panes interface**

```python
# test/test_terminal_list_panes.py
from terminal import TerminalBackend

def test_terminal_backend_has_list_panes_method():
    """TerminalBackend should have list_panes abstract method"""
    assert hasattr(TerminalBackend, 'list_panes')
    # 验证是 callable
    assert callable(getattr(TerminalBackend, 'list_panes', None))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_terminal_list_panes.py::test_terminal_backend_has_list_panes_method -v`
Expected: FAIL with "AttributeError: type object 'TerminalBackend' has no attribute 'list_panes'"

- [ ] **Step 3: Add list_panes abstract method to TerminalBackend**

```python
# lib/terminal.py (在 TerminalBackend 类中添加)
class TerminalBackend(ABC):
    @abstractmethod
    def send_text(self, pane_id: str, text: str) -> None: ...
    @abstractmethod
    def is_alive(self, pane_id: str) -> bool: ...
    @abstractmethod
    def kill_pane(self, pane_id: str) -> None: ...
    @abstractmethod
    def activate(self, pane_id: str) -> None: ...
    @abstractmethod
    def create_pane(self, cmd: str, cwd: str, direction: str = "right", percent: int = 50, parent_pane: Optional[str] = None) -> str: ...
    
    @abstractmethod
    def list_panes(self) -> list[dict]:
        """
        返回所有 pane 的信息列表
        
        每个 dict 至少包含:
        - pane_id: str
        - title: str (用于 marker 匹配)
        
        用于阶段 2 liveness probe
        
        Returns:
            pane 信息列表
        """
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test/test_terminal_list_panes.py::test_terminal_backend_has_list_panes_method -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/terminal.py test/test_terminal_list_panes.py
git commit -m "feat(terminal): add list_panes() abstract method to TerminalBackend"
```

### Task 8: terminal.py - WeztermBackend 实现 list_panes()

**Files:**
- Modify: `lib/terminal.py:537-634` (WeztermBackend 类，将 _list_panes 改为 public)
- Test: `test/test_terminal_list_panes.py` (扩展)

- [ ] **Step 1: Write failing test for WeztermBackend.list_panes**

```python
# test/test_terminal_list_panes.py (追加)
from terminal import WeztermBackend

def test_wezterm_backend_has_public_list_panes():
    """WeztermBackend should have public list_panes method"""
    backend = WeztermBackend()
    assert hasattr(backend, 'list_panes')
    assert callable(backend.list_panes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_terminal_list_panes.py::test_wezterm_backend_has_public_list_panes -v`
Expected: FAIL with "AttributeError: 'WeztermBackend' object has no attribute 'list_panes'"

- [ ] **Step 3: Rename _list_panes to list_panes (make public)**

```python
# lib/terminal.py (在 WeztermBackend 类中，找到 _list_panes 方法，重命名为 list_panes)
class WeztermBackend(TerminalBackend):
    # ... 其他方法 ...
    
    def list_panes(self) -> list[dict]:
        """
        返回所有 WezTerm pane 的信息列表
        
        通过 wezterm cli list --format json 获取
        每个 dict 包含: pane_id, window_id, tab_id, title, cwd 等
        
        Returns:
            pane 信息列表
        """
        try:
            result = _run(
                [*self._cli_base_args(), "list", "--format", "json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                return []
            panes = json.loads(result.stdout)
            return panes if isinstance(panes, list) else []
        except Exception:
            return []
    
    # 删除原来的 _list_panes 方法（如果存在）
    # 注意：原来的 _pane_id_by_title_marker 和 _pane_id_by_cwd 等内部方法可能调用 _list_panes
    # 需要将它们改为调用 list_panes
```

注意：需要搜索 WeztermBackend 中所有调用 `_list_panes()` 的地方，改为 `list_panes()`：

```python
# lib/terminal.py (WeztermBackend 类中，搜索所有 _list_panes 调用)
# 例如在 _pane_id_by_title_marker 中：
def _pane_id_by_title_marker(self, panes: list[dict], marker: str) -> Optional[str]:
    # 这个方法接收 panes 参数，不需要改
    
# 但在 find_pane_by_title_marker 中：
def find_pane_by_title_marker(self, marker: str) -> Optional[str]:
    panes = self.list_panes()  # 改为 list_panes
    return self._pane_id_by_title_marker(panes, marker)

# 在 is_alive 中：
def is_alive(self, pane_id: str) -> bool:
    panes = self.list_panes()  # 改为 list_panes
    # ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test/test_terminal_list_panes.py::test_wezterm_backend_has_public_list_panes -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/terminal.py test/test_terminal_list_panes.py
git commit -m "feat(terminal): make WeztermBackend.list_panes() public"
```

### Task 9: terminal.py - TmuxBackend/Iterm2Backend 实现 list_panes()

**Files:**
- Modify: `lib/terminal.py:218-522` (TmuxBackend 类)
- Modify: `lib/terminal.py:523-536` (Iterm2Backend 类)
- Test: `test/test_terminal_list_panes.py` (扩展)

- [ ] **Step 1: Write failing test for TmuxBackend.list_panes**

```python
# test/test_terminal_list_panes.py (追加)
from terminal import TmuxBackend, Iterm2Backend

def test_tmux_backend_has_list_panes():
    """TmuxBackend should have list_panes method"""
    backend = TmuxBackend()
    assert hasattr(backend, 'list_panes')
    assert callable(backend.list_panes)

def test_iterm2_backend_has_list_panes():
    """Iterm2Backend should have list_panes method"""
    backend = Iterm2Backend()
    assert hasattr(backend, 'list_panes')
    assert callable(backend.list_panes)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test/test_terminal_list_panes.py::test_tmux_backend_has_list_panes test/test_terminal_list_panes.py::test_iterm2_backend_has_list_panes -v`
Expected: FAIL with "AttributeError"

- [ ] **Step 3: Implement TmuxBackend.list_panes**

```python
# lib/terminal.py (在 TmuxBackend 类中添加)
class TmuxBackend(TerminalBackend):
    # ... 其他方法 ...
    
    def list_panes(self) -> list[dict]:
        """
        返回所有 tmux pane 的信息列表
        
        通过 tmux list-panes -a 获取
        每个 dict 包含: pane_id, title
        
        Returns:
            pane 信息列表
        """
        try:
            result = _run(
                ["tmux", "list-panes", "-a", "-F", "#{pane_id}\t#{pane_title}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                return []
            
            panes = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t", 1)
                if len(parts) >= 1:
                    panes.append({
                        "pane_id": parts[0],
                        "title": parts[1] if len(parts) > 1 else "",
                    })
            return panes
        except Exception:
            return []
```

- [ ] **Step 4: Implement Iterm2Backend.list_panes**

```python
# lib/terminal.py (在 Iterm2Backend 类中添加)
class Iterm2Backend(TerminalBackend):
    # ... 其他方法 ...
    
    def list_panes(self) -> list[dict]:
        """
        返回所有 iTerm2 pane 的信息列表
        
        通过 osascript 获取
        每个 dict 包含: pane_id, title
        
        Returns:
            pane 信息列表
        """
        # iTerm2 的 pane 列表获取比较复杂，暂时返回空列表
        # 后续可以根据需要实现
        return []
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest test/test_terminal_list_panes.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add lib/terminal.py test/test_terminal_list_panes.py
git commit -m "feat(terminal): implement list_panes() for TmuxBackend and Iterm2Backend"
```

---

## Phase 4: Session Loading (依赖 Phase 2, 3)

### Task 10: caskd_session - 增加 ccb_session_id property

**Files:**
- Modify: `lib/caskd_session.py:45-95` (CodexProjectSession dataclass)
- Test: `test/test_caskd_session_routing.py` (新建)

- [ ] **Step 1: Write failing test for ccb_session_id property**

```python
# test/test_caskd_session_routing.py
from pathlib import Path
from caskd_session import CodexProjectSession

def test_codex_project_session_has_ccb_session_id_property(tmp_path):
    """CodexProjectSession should have ccb_session_id property"""
    session_file = tmp_path / ".codex-session"
    session_file.write_text("{}", encoding="utf-8")
    
    data = {
        "session_id": "ai-1716890000-12345",
        "runtime_dir": str(tmp_path),
        "terminal": "tmux",
    }
    
    session = CodexProjectSession(session_file=session_file, data=data)
    assert session.ccb_session_id == "ai-1716890000-12345"

def test_codex_project_session_ccb_session_id_from_data(tmp_path):
    """ccb_session_id should read from data['session_id']"""
    session_file = tmp_path / ".codex-session"
    session_file.write_text("{}", encoding="utf-8")
    
    # 使用 ccb_session_id 字段（新格式）
    data1 = {"ccb_session_id": "new-format-id"}
    session1 = CodexProjectSession(session_file=session_file, data=data1)
    assert session1.ccb_session_id == "new-format-id"
    
    # 使用 session_id 字段（旧格式兼容）
    data2 = {"session_id": "old-format-id"}
    session2 = CodexProjectSession(session_file=session_file, data=data2)
    assert session2.ccb_session_id == "old-format-id"
    
    # 优先使用 ccb_session_id
    data3 = {"ccb_session_id": "new", "session_id": "old"}
    session3 = CodexProjectSession(session_file=session_file, data=data3)
    assert session3.ccb_session_id == "new"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test/test_caskd_session_routing.py -v`
Expected: FAIL with "AttributeError: 'CodexProjectSession' object has no attribute 'ccb_session_id'"

- [ ] **Step 3: Add ccb_session_id property to CodexProjectSession**

```python
# lib/caskd_session.py (在 CodexProjectSession dataclass 中添加 property)
@dataclass
class CodexProjectSession:
    session_file: Path
    data: dict

    @property
    def ccb_session_id(self) -> str:
        """
        CCB launcher session ID（优先 ccb_session_id 字段，兼容旧 session_id 字段）
        
        注意：这不是 Codex 自身的 session ID（codex_session_id），而是 CCB launcher 的实例 ID
        """
        # 优先使用 ccb_session_id（新格式）
        value = self.data.get("ccb_session_id")
        if value:
            return str(value).strip()
        # 兼容旧格式（session_id 字段）
        value = self.data.get("session_id")
        return str(value or "").strip()
    
    # ... 其他 properties ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test/test_caskd_session_routing.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add lib/caskd_session.py test/test_caskd_session_routing.py
git commit -m "feat(caskd_session): add ccb_session_id property to CodexProjectSession"
```

### Task 11: caskd_session - 修改 compute_session_key 优先使用 ccb_session_id

**Files:**
- Modify: `lib/caskd_session.py:237-250` (compute_session_key 函数)
- Test: `test/test_caskd_session_routing.py` (扩展)

- [ ] **Step 1: Write failing test for compute_session_key priority**

```python
# test/test_caskd_session_routing.py (追加)
from caskd_session import compute_session_key

def test_compute_session_key_prioritizes_ccb_session_id(tmp_path):
    """compute_session_key should prioritize ccb_session_id over marker/pane_id"""
    session_file = tmp_path / ".codex-session"
    session_file.write_text("{}", encoding="utf-8")
    
    data = {
        "session_id": "ai-1716890000-12345",
        "pane_id": "%42",
        "pane_title_marker": "CCB-Codex",
    }
    
    session = CodexProjectSession(session_file=session_file, data=data)
    key = compute_session_key(session)
    
    # 应该使用 ccb_session_id，而不是 marker 或 pane_id
    assert key == "ccb:ai-1716890000-12345"

def test_compute_session_key_falls_back_to_pane_id(tmp_path):
    """compute_session_key should fall back to pane_id if no ccb_session_id"""
    session_file = tmp_path / ".codex-session"
    session_file.write_text("{}", encoding="utf-8")
    
    data = {
        "pane_id": "%42",
        "pane_title_marker": "CCB-Codex",
    }
    
    session = CodexProjectSession(session_file=session_file, data=data)
    key = compute_session_key(session)
    
    assert key == "codex_pane:%42"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test/test_caskd_session_routing.py::test_compute_session_key_prioritizes_ccb_session_id -v`
Expected: FAIL (current implementation prioritizes marker)

- [ ] **Step 3: Rewrite compute_session_key to prioritize ccb_session_id**

```python
# lib/caskd_session.py (完全重写 compute_session_key 函数)
def compute_session_key(session: CodexProjectSession) -> str:
    """
    计算 session 的唯一 worker key
    
    优先级:
    1. ccb_session_id (CCB launcher session ID，稳定唯一)
    2. pane_id (运行时标识)
    3. pane_title_marker (辅助重发现)
    4. session_file (最后 fallback)
    
    注意：优先使用 ccb_session_id，避免不同实例共享同一个 marker 导致 worker 合并
    """
    # 优先用 CCB launcher session id（稳定唯一身份）
    ccb_sid = session.ccb_session_id
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test/test_caskd_session_routing.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add lib/caskd_session.py test/test_caskd_session_routing.py
git commit -m "feat(caskd_session): prioritize ccb_session_id in compute_session_key"
```

---

由于计划内容较长，我将继续在后续消息中提供剩余任务。当前已完成的 Phase 1-4 部分涵盖了：
- Protocol layer (Task 1-3)
- Registry cleanup (Task 4)
- Core routing (Task 5-6)
- Terminal backend (Task 7-9)
- Session loading foundation (Task 10-11)

让我继续编写剩余任务...
