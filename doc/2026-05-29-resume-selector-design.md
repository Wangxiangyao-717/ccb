# CCB Resume Selector - Interactive Session Picker Design

## Overview

Enhance `ccb up -r` to show an interactive TUI for selecting which historical session to resume, instead of always resuming the latest one.

## Problem

When working on a single project directory, users often start multiple CCB sessions (`.codex-session`, `.codex-session-1`, etc.). The current `ccb -r` only resumes the latest session, forcing users to manually navigate into each terminal and use `/resume` to restore the correct context.

## Solution

Add an interactive Textual TUI that:
1. Lists all historical CCB sessions in the current directory
2. Shows key metadata: start time, providers used, first user message
3. Allows search/filter by text
4. Provides a detail view showing all user messages from a session
5. Returns the selected session ID to `ccb up` for restoration

## Command Line Interface

### Usage

```bash
# Interactive selection with specific profile
ccb up -C dpsk -r

# Interactive selection with default claude
ccb up -r

# Interactive selection with multiple providers
ccb up -r codex gemini

# Non-interactive (existing behavior): resume latest
ccb up
```

### Flow

1. User runs `ccb up -C <profile> -r [providers...]`
2. CCB scans current directory for all `.codex-session*` files
3. Textual TUI launches with session list
4. User filters/searches, selects session, presses Enter
5. CCB starts with selected session using specified profile and providers

## UI Design

### Main View (Session List)

```
┌─────────────────────────────────────────────────────────┐
│  Search: refact                                    [3/12] │
├─────────────────────────────────────────────────────────┤
│  DATE        TIME    PROVIDERS       TOPIC              │
│    05-29     10:47   codex           Refactor auth...   │
│    05-28     15:30   codex,gemini    Fix the login...   │
│  > 05-27     09:15   codex           Add unit tests...  │
│    05-26     14:20   codex,opencode  Database layer...  │
│    ...                                                   │
├─────────────────────────────────────────────────────────┤
│  Enter: resume  |  →: detail  |  ↑↓: navigate  |  Esc  │
└─────────────────────────────────────────────────────────┘
```

**Columns:**
- **DATE**: Session start date (MM-DD format)
- **TIME**: Session start time (HH:MM format)
- **PROVIDERS**: Comma-separated list of providers used (codex/gemini/opencode)
- **TOPIC**: First user message, truncated to ~40 chars

**Navigation:**
- ↑/↓: Move selection
- Enter: Resume selected session
- →: Enter detail view
- Esc: Cancel and exit
- Text input: Filter sessions by search term

### Detail View (User Messages)

```
┌─────────────────────────────────────────────────────────┐
│  Session: ai-1780022775-10724  05-27 09:15  8 msgs      │
├─────────────────────────────────────────────────────────┤
│  ─────────────────────────────────────────────────────── │
│                                                         │
│   1. 帮我给 auth 模块加上单元测试                         │
│   2. 测试覆盖不够，login 和 logout 都要测                 │
│   3. 用 pytest fixture 来 mock database                 │
│   4. 再加一个测试：token 过期的场景                       │
│   5. 跑一下测试看看全过了没                               │
│   6. 好的，把测试文件移到 tests/ 目录下                   │
│   7. 提交代码                                            │
│   8. 推送到 main                                         │
│                                                         │
├─────────────────────────────────────────────────────────┤
│  3/8    ← 返回列表    ↑↓ 滚动消息                        │
└─────────────────────────────────────────────────────────┘
```

**Navigation:**
- ↑/↓: Scroll messages
- ←/Esc: Return to session list

## Data Model

### Session Files

Each provider creates its own session file. They are linked by the same `session_id` (CCB session ID).

```
.codex-session      → codex_session_id, codex_session_path
.gemini-session     → gemini_session_id (if gemini provider used)
.opencode-session   → opencode_session_id (if opencode provider used)
```

Example (same CCB session, three files):
```
.codex-session-6:
{
    "session_id": "ai-1779953764-49764",
    "claude_session_id": "a75f14a4-5063-4033-9275-84aff9a8f815",  # Written to ALL provider files
    "codex_session_id": "019ddea0-6a6a-7cd3-9d15-507fae103b58",
    "codex_session_path": "C:\\...\\rollout-...jsonl",
    "started_at": "2026-05-29 10:47:30",
    "work_dir": "E:\\ccb",
    ...
}

.gemini-session-6:
{
    "session_id": "ai-1779953764-49764",     # Same CCB session ID
    "claude_session_id": "a75f14a4-...",      # Same Claude UUID (written to all files)
    "gemini_session_id": "...",
    "started_at": "2026-05-29 10:47:30",
    ...
}
```

### Resume Selector Data Collection

The selector scans ALL provider session files, groups them by `session_id`:

```python
def _load_sessions(self) -> list[dict]:
    """Scan all provider session files and group by CCB session_id."""
    # Collect all provider session files
    all_files = []
    for provider in ["codex", "gemini", "opencode"]:
        all_files.extend(self.work_dir.glob(f".{provider}-session*"))
    
    # Group by session_id
    groups: dict[str, dict] = {}
    for session_file in all_files:
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            sid = data.get("session_id", "")
            if not sid:
                continue
            
            if sid not in groups:
                groups[sid] = {
                    "session_id": sid,
                    "started_at": data.get("started_at", ""),
                    "providers": [],
                    "claude_session_id": data.get("claude_session_id"),
                    "session_files": {},  # provider -> file path
                }
            
            # Detect which provider this file belongs to
            provider = self._detect_provider(session_file, data)
            if provider and provider not in groups[sid]["providers"]:
                groups[sid]["providers"].append(provider)
                groups[sid]["session_files"][provider] = session_file
                
                # Merge claude_session_id from any file that has it
                if not groups[sid]["claude_session_id"]:
                    groups[sid]["claude_session_id"] = data.get("claude_session_id")
                    
        except Exception:
            continue
    
    # Convert to list and sort
    sessions = list(groups.values())
    sessions.sort(key=lambda s: s["started_at"], reverse=True)
    
    # Load first user message for each session
    for session in sessions:
        jsonl_path = self._get_jsonl_path(session)
        if jsonl_path:
            messages = extract_user_messages(jsonl_path)
            if messages:
                session["first_message"] = messages[0][:40] + "..." if len(messages[0]) > 40 else messages[0]
    
    return sessions
```

### Resume Flow

When user selects a session and presses Enter:

1. Get the selected `session_id`
2. Find all provider files with that `session_id`
3. From those files, extract:
   - `claude_session_id` → `claude --resume <uuid>`
   - `codex_session_id` → `codex resume <uuid>`
   - `gemini_session_id` → `gemini --resume <id>`
   - `opencode_session_id` → `opencode --continue <id>`
4. Only resume providers that the user requested in `ccb up -r [providers...]`

### Recording Claude Session UUID

When `ccb up` starts Claude, the JSONL file is created. We capture its UUID and write it back to the session file.

In `_start_claude()`, after Claude subprocess starts:

```python
def _start_claude(self) -> int:
    # ... existing code ...
    
    try:
        returncode = subprocess.run(cmd, env=env).returncode
        
        # Record Claude session UUID after Claude exits
        claude_uuid = self._detect_new_claude_session()
        if claude_uuid:
            for provider in self.providers:
                session_file = self.session_files.get(provider)
                if session_file and session_file.exists():
                    data = self._read_json_file(session_file)
                    data["claude_session_id"] = claude_uuid
                    self._write_json_file(session_file, data)
        
        return returncode
    except KeyboardInterrupt:
        return 130

def _detect_new_claude_session(self) -> Optional[str]:
    """Find the newest JSONL file created during this CCB session.
    
    Warning: This is a best-effort detection using mtime. In concurrent scenarios
    (e.g., two CCB instances started at the same time in different tabs), this
    may pick up the wrong JSONL file and write an incorrect UUID into the session file.
    
    Mitigation:
    - Concurrent CCB instances in the same directory are rare in practice
    - Users can manually edit the session file if resume picks the wrong session
    - Future improvement: Add a confirmation prompt when resuming to let users verify the selected session
    """
    project_dir = self._claude_project_dir(Path.cwd())
    if not project_dir.exists():
        return None
    
    # Find JSONL files created after CCB started
    jsonl_files = list(project_dir.glob("*.jsonl"))
    ccb_start_time = self.session_start_time  # Record this in __init__
    
    new_sessions = [
        f for f in jsonl_files 
        if f.stat().st_mtime >= ccb_start_time
        and f.stat().st_size > 0
    ]
    
    if not new_sessions:
        return None
    
    # Return the newest one (most likely ours)
    latest = max(new_sessions, key=lambda f: f.stat().st_mtime)
    return latest.stem  # UUID without .jsonl extension
```

**Note on provider session ID backfill timing:**

The `gemini_session_id` and `opencode_session_id` fields are not written at session startup. They are backfilled later when the corresponding daemon (gaskd/oaskd) starts handling requests. This means:

1. A freshly started session may not have these fields yet
2. When resuming, if these fields are missing, we fall back to the existing "resume latest" logic
3. **Risk**: In the context of the resume selector, "resume latest" means restoring a different session than what the user selected. This is not ideal but is better than failing entirely.
4. **Mitigation**: Show a warning to users when falling back: "Provider X session ID not recorded, resuming latest session instead of selected session"
5. Future improvement: Write provider session IDs at startup to eliminate this issue

### User Message Extraction

From `~/.claude/projects/<project-dir>/<uuid>.jsonl`:

```python
def extract_user_messages(session_file: Path) -> list[str]:
    messages = []
    for line in session_file.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if d.get("type") != "user":
            continue
        content = d.get("message", {}).get("content", "")
        # Handle multimodal content (list of blocks)
        if isinstance(content, list):
            texts = [b.get("text", "") for b in content 
                     if isinstance(b, dict) and b.get("type") == "text"]
            content = " ".join(texts)
        # Strip XML tags and normalize whitespace
        clean = re.sub(r"<[^>]+>", " ", content)
        clean = re.sub(r"\s+", " ", clean).strip()
        if clean:
            messages.append(clean)
    return messages
```

### Session-to-JSONL Mapping

The `claude_session_id` field in `.codex-session*` files directly contains the Claude JSONL UUID. For new sessions, this provides direct lookup without mtime guessing.

For legacy session files (created before this feature was added), we fall back to mtime matching as a best-effort approach.

The complete implementation is in the `ResumeSelectorApp._get_jsonl_path()` method shown in the Implementation section below.

## Implementation

### New Files

**`lib/resume_selector.py`** - Textual TUI implementation

```python
from textual.app import App, ComposeResult
from textual.widgets import ListView, ListItem, Input, Static, Footer
from textual.containers import Container
from textual.binding import Binding
from pathlib import Path
import json
import re

class ResumeSelectorApp(App):
    """Textual app for selecting a CCB session to resume."""
    
    # CSS is inline for simplicity; could be moved to separate .tcss file
    CSS = """
    Screen {
        layout: vertical;
    }
    
    #search {
        dock: top;
        height: 3;
        margin: 1;
    }
    
    #session-list {
        height: 1fr;
    }
    
    ListItem {
        padding: 0 1;
    }
    
    ListItem:hover {
        background: $accent;
    }
    
    #detail-header {
        dock: top;
        height: 1;
        padding: 0 1;
        background: $surface;
    }
    
    #separator {
        dock: top;
        height: 1;
    }
    
    #message-list {
        height: 1fr;
    }
    """
    
    BINDINGS = [
        Binding("escape", "quit", "Cancel"),
        Binding("right", "detail", "Detail view", show=False),
    ]
    
    def __init__(self, work_dir: Path):
        super().__init__()
        self.work_dir = work_dir
        self.sessions = self._load_sessions()
        self.selected_session_id = None
    
    def _load_sessions(self) -> list[dict]:
        """Scan all provider session files and group by CCB session_id."""
        # Collect all provider session files
        all_files = []
        for provider in ["codex", "gemini", "opencode"]:
            all_files.extend(self.work_dir.glob(f".{provider}-session*"))
        
        # Group by session_id
        groups: dict[str, dict] = {}
        for session_file in all_files:
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
                sid = data.get("session_id", "")
                if not sid:
                    continue
                
                if sid not in groups:
                    groups[sid] = {
                        "session_id": sid,
                        "started_at": data.get("started_at", ""),
                        "providers": [],
                        "claude_session_id": data.get("claude_session_id"),
                        "session_files": {},  # provider -> file path
                    }
                
                # Detect which provider this file belongs to
                provider = self._detect_provider(session_file)
                if provider and provider not in groups[sid]["providers"]:
                    groups[sid]["providers"].append(provider)
                    groups[sid]["session_files"][provider] = session_file
                    
                    # Merge claude_session_id from any file that has it
                    if not groups[sid]["claude_session_id"]:
                        groups[sid]["claude_session_id"] = data.get("claude_session_id")
                        
            except Exception:
                continue
        
        # Convert to list and sort by started_at descending (newest first)
        sessions = list(groups.values())
        sessions.sort(key=lambda s: s["started_at"], reverse=True)
        
        # Load first user message for each session
        for session in sessions:
            jsonl_path = self._get_jsonl_path(session)
            if jsonl_path:
                messages = extract_user_messages(jsonl_path)
                if messages:
                    session["first_message"] = messages[0][:40] + "..." if len(messages[0]) > 40 else messages[0]
        
        return sessions
    
    def _detect_provider(self, session_file: Path) -> str:
        """Detect provider from session file name."""
        name = session_file.name
        if name.startswith(".codex-session"):
            return "codex"
        elif name.startswith(".gemini-session"):
            return "gemini"
        elif name.startswith(".opencode-session"):
            return "opencode"
        return ""
    
    def _get_jsonl_path(self, session_data: dict) -> Optional[Path]:
        """Get Claude JSONL path from session data.
        
        Priority:
        1. Use claude_session_id if available (direct lookup)
        2. Fall back to mtime matching for old session files
        """
        # Try direct lookup using claude_session_id
        claude_uuid = session_data.get("claude_session_id")
        if claude_uuid:
            project_dir = self._get_claude_project_dir()
            jsonl_file = project_dir / f"{claude_uuid}.jsonl"
            if jsonl_file.exists():
                return jsonl_file
            return None
        
        # Fallback: mtime matching for legacy session files
        started_at = session_data.get("started_at")
        if started_at:
            return self._find_jsonl_by_mtime(started_at)
        
        return None
    
    def _get_claude_project_dir(self) -> Path:
        """Get Claude project directory for current working directory.
        
        This matches the logic in AILauncher._claude_project_dir().
        Claude uses a filesystem-friendly key derived from the working directory path.
        """
        from pathlib import Path
        import re
        import os
        
        projects_root = Path.home() / ".claude" / "projects"
        
        # Try multiple candidates to handle symlinked paths
        candidates = []
        env_pwd = os.environ.get("PWD")
        if env_pwd:
            candidates.append(Path(env_pwd))
        candidates.append(self.work_dir)
        try:
            candidates.append(self.work_dir.resolve())
        except Exception:
            pass
        
        # Find existing project directory
        for candidate in candidates:
            key = re.sub(r"[^A-Za-z0-9]", "-", str(candidate))
            project_dir = projects_root / key
            if project_dir.exists():
                return project_dir
        
        # Fallback to best-effort key (match ccb's try/except pattern)
        try:
            fallback_path = self.work_dir.resolve()
        except Exception:
            fallback_path = self.work_dir
        key = re.sub(r"[^A-Za-z0-9]", "-", str(fallback_path))
        return projects_root / key
    
    def _find_jsonl_by_mtime(self, started_at: str) -> Optional[Path]:
        """Find JSONL file by matching started_at timestamp.
        
        This is a fallback for old session files that don't have claude_session_id.
        """
        from datetime import datetime
        
        project_dir = self._get_claude_project_dir()
        if not project_dir.exists():
            return None
        
        # Parse started_at timestamp
        try:
            target_time = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S").timestamp()
        except:
            return None
        
        # Find JSONL file with closest mtime within 1 hour window
        jsonl_files = list(project_dir.glob("*.jsonl"))
        if not jsonl_files:
            return None
        
        best_match = None
        best_diff = float('inf')
        
        for jsonl_file in jsonl_files:
            mtime = jsonl_file.stat().st_mtime
            diff = abs(mtime - target_time)
            if diff < best_diff and diff < 3600:  # Within 1 hour
                best_diff = diff
                best_match = jsonl_file
        
        return best_match
    
    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search...", id="search")
        yield ListView(id="session-list")
        yield Footer()
    
    def on_mount(self):
        self._refresh_list()
    
    def _refresh_list(self, filter_text: str = ""):
        """Update session list based on search filter."""
        # Filter sessions and update ListView...
        pass
    
    def action_detail(self):
        """Push detail screen for selected session."""
        # Push DetailScreen...
        pass
    
    def on_list_view_selected(self, event: ListView.Selected):
        """Handle Enter key - resume selected session."""
        self.selected_session_id = event.item.session_id
        self.exit()

class DetailScreen(Screen):
    """Detail view showing all user messages in a session."""
    
    BINDINGS = [
        Binding("escape", "pop_screen", "Back"),
        Binding("left", "pop_screen", "Back"),
    ]
    
    def __init__(self, session: dict):
        super().__init__()
        self.session = session
        self.messages = extract_user_messages(session["jsonl_path"])
    
    def compose(self) -> ComposeResult:
        yield Static(self._header(), id="detail-header")
        yield Static("─" * 60, id="separator")
        yield ListView(id="message-list")
        yield Footer()
    
    def on_mount(self):
        # Populate message list...
        pass

def show_resume_selector(work_dir: Path) -> Optional[str]:
    """Launch TUI and return selected session ID, or None if cancelled."""
    app = ResumeSelectorApp(work_dir)
    app.run()
    return app.selected_session_id
```

### Modified Files

**`ccb`** - Main CCB script

Changes to `cmd_up()` function (around line 1644):

```python
def cmd_up(args):
    providers = _parse_providers(args.providers or ["codex"])
    if not providers:
        return 2
    
    if args.resume:
        # Launch interactive selector
        from resume_selector import show_resume_selector
        selected_session_id = show_resume_selector(Path.cwd())
        if not selected_session_id:
            return 0  # User cancelled
    else:
        selected_session_id = None
    
    launcher = AILauncher(
        providers=providers,
        resume=args.resume,
        resume_session_id=selected_session_id,  # New parameter
        claude_cmd=args.claude_cmd,
    )
    return launcher.run_up()
```

Changes to `AILauncher.__init__()` (around line 223):

```python
def __init__(
    self,
    providers: list,
    resume: bool = False,
    resume_session_id: Optional[str] = None,  # New parameter
    auto: bool = False,
    claude_cmd: str = None,
):
    self.providers = providers or ["codex"]
    self.resume = resume
    self.resume_session_id = resume_session_id  # Store for later use
    self.session_start_time = time.time()  # Record start time for UUID detection
    # ... rest of init
```

New helper methods in `AILauncher`:

```python
def _find_session_file_by_ccb_id(self, ccb_session_id: str) -> Optional[Path]:
    """Find session file by CCB session ID."""
    for session_file in Path.cwd().glob(".*-session*"):
        if not session_file.is_file():
            continue
        try:
            data = self._read_json_file(session_file)
            if data.get("session_id") == ccb_session_id:
                return session_file
        except:
            continue
    return None

def _find_jsonl_uuid_by_mtime(self, started_at: str) -> Optional[str]:
    """Find Claude JSONL UUID by matching started_at timestamp.
    
    This is a fallback for legacy sessions that don't have claude_session_id recorded.
    """
    from datetime import datetime
    
    project_dir = self._claude_project_dir(Path.cwd())
    if not project_dir.exists():
        return None
    
    # Parse started_at timestamp
    try:
        target_time = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S").timestamp()
    except:
        return None
    
    # Find JSONL files
    jsonl_files = list(project_dir.glob("*.jsonl"))
    if not jsonl_files:
        return None
    
    # Find closest match within 1 hour window
    best_match = None
    best_diff = float('inf')
    
    for jsonl_file in jsonl_files:
        mtime = jsonl_file.stat().st_mtime
        diff = abs(mtime - target_time)
        if diff < best_diff and diff < 3600:  # Within 1 hour
            best_diff = diff
            best_match = jsonl_file
    
    if best_match:
        return best_match.stem  # Return UUID without .jsonl extension
    return None

def _detect_new_claude_session(self) -> Optional[str]:
    """Find the newest JSONL file created during this CCB session."""
    project_dir = self._claude_project_dir(Path.cwd())
    if not project_dir.exists():
        return None
    
    jsonl_files = list(project_dir.glob("*.jsonl"))
    
    # Find JSONL files created after CCB started
    new_sessions = [
        f for f in jsonl_files 
        if f.stat().st_mtime >= self.session_start_time
        and f.stat().st_size > 0
    ]
    
    if not new_sessions:
        return None
    
    # Return the newest one (most likely ours)
    latest = max(new_sessions, key=lambda f: f.stat().st_mtime)
    return latest.stem  # UUID without .jsonl extension
```

Changes to `_start_claude()` (around line 1513):

```python
def _start_claude(self) -> int:
    # ... existing code ...
    
    if self.resume:
        # Load session data (collects from all provider files)
        self.resume_session_data = self._load_resume_session_data()
        
        if self.resume_session_data:
            claude_uuid = self.resume_session_data.get("claude_session_id")
            
            if claude_uuid:
                # Direct resume with UUID
                cmd.extend(["--resume", claude_uuid])
                print(f"🔁 Resuming Claude session {claude_uuid[:8]}...")
            else:
                # Legacy session without claude_session_id - try mtime fallback
                started_at = None
                # Find started_at from any provider file
                for provider_file in self.resume_session_data.get("session_files", {}).values():
                    data = self._read_json_file(provider_file)
                    if data and data.get("started_at"):
                        started_at = data.get("started_at")
                        break
                
                legacy_uuid = self._find_jsonl_uuid_by_mtime(started_at) if started_at else None
                
                if legacy_uuid:
                    cmd.extend(["--resume", legacy_uuid])
                    print(f"🔁 Resuming Claude session {legacy_uuid[:8]} (found via mtime)...")
                    
                    # Record the UUID for future use in all provider files
                    for provider_file in self.resume_session_data.get("session_files", {}).values():
                        data = self._read_json_file(provider_file)
                        if data:
                            data["claude_session_id"] = legacy_uuid
                            self._write_json_file(provider_file, data)
                else:
                    cmd.append("--continue")
                    print(f"⚠️ Could not find matching session, resuming latest...")
        else:
            # Fallback to latest (existing behavior)
            _, has_history = self._get_latest_claude_session_id()
            if has_history:
                cmd.append("--continue")
    
    # ... rest of function ...
    
    try:
        returncode = subprocess.run(cmd, env=env).returncode
        
        # Record Claude session UUID after Claude exits
        if not self.resume:  # Only record for new sessions
            claude_uuid = self._detect_new_claude_session()
            if claude_uuid:
                for provider in self.providers:
                    session_file = self.session_files.get(provider)
                    if session_file and session_file.exists():
                        data = self._read_json_file(session_file)
                        data["claude_session_id"] = claude_uuid
                        self._write_json_file(session_file, data)
        
        return returncode
    except KeyboardInterrupt:
        return 130
```

### Provider Resume Logic

When resuming a selected session, each provider's session ID is read from its own session file:

```python
def _load_resume_session_data(self) -> Optional[dict]:
    """Load session data for the selected CCB session.
    
    Returns a dict with:
    - session_id: CCB session ID
    - claude_session_id: Claude JSONL UUID (from any provider file that has it)
    - session_files: {provider: path} mapping
    """
    if not self.resume or not self.resume_session_id:
        return None
    
    # Find all provider files for this session_id
    session_files = {}
    claude_session_id = None
    
    for provider in ["codex", "gemini", "opencode"]:
        for f in Path.cwd().glob(f".{provider}-session*"):
            try:
                data = self._read_json_file(f)
                if data.get("session_id") == self.resume_session_id:
                    session_files[provider] = f
                    if not claude_session_id:
                        claude_session_id = data.get("claude_session_id")
                    break
            except:
                continue
    
    if not session_files:
        return None
    
    return {
        "session_id": self.resume_session_id,
        "claude_session_id": claude_session_id,
        "session_files": session_files,
    }

def _get_provider_session_id(self, provider: str) -> Optional[str]:
    """Read provider-specific session ID from its session file."""
    if not self.resume_session_data:
        return None
    
    session_file = self.resume_session_data.get("session_files", {}).get(provider)
    if not session_file or not session_file.exists():
        return None
    
    data = self._read_json_file(session_file)
    if not data:
        return None
    
    # Each provider file has its own session ID field
    key = f"{provider}_session_id"
    return data.get(key)
```

Updated `_build_codex_start_cmd()`:

```python
def _build_codex_start_cmd(self) -> str:
    """Build Codex start command, using recorded session ID if resuming."""
    cmd = "codex -c=disable_paste_burst=true --dangerously-bypass-approvals-and-sandbox" if self.auto else "codex -c=disable_paste_burst=true"
    
    # Check if we have recorded session data
    if self.resume:
        codex_uuid = self._get_provider_session_id("codex")
        if codex_uuid:
            cmd = f"{cmd} resume {codex_uuid}"
            print(f"🔁 Resuming Codex session {codex_uuid[:8]}...")
            return cmd
        
        # Fallback: try to find latest session (existing logic)
        session_id, has_history = self._get_latest_codex_session_id()
        if session_id:
            cmd = f"{cmd} resume {session_id}"
            print(f"🔁 {t('resuming_session', provider='Codex', session_id=session_id[:8])}")
        else:
            print(f"ℹ️ {t('no_history_fresh', provider='Codex')}")
    
    return cmd
```

Similarly for Gemini and OpenCode:

```python
def _build_gemini_start_cmd(self) -> str:
    if self.resume:
        gemini_id = self._get_provider_session_id("gemini")
        if gemini_id:
            return f"gemini --resume {gemini_id}"
    # Fallback to existing logic...

def _build_opencode_start_cmd(self) -> str:
    if self.resume:
        opencode_id = self._get_provider_session_id("opencode")
        if opencode_id:
            return f"opencode --continue {opencode_id}"
    # Fallback to existing logic...
```

## Dependencies

Add `textual` to dependencies:

**`install.ps1`** (Windows):
```powershell
pip install textual
```

**`install.sh`** (Linux/macOS):
```bash
pip install textual
```

## Error Handling

**No sessions found:**
- Show message: "No CCB sessions found in this directory"
- Wait 2 seconds, then exit

**Session file is malformed:**
- Skip the file, log warning to stderr
- Continue loading other sessions

**No JSONL file found:**
- Session still appears in list
- Detail view shows: "No conversation data available"

**Textual not installed:**
- Catch ImportError in `ccb`
- Show: "Error: textual library not installed. Run: pip install textual"
- Exit with code 1

**User presses Esc:**
- Return None from `show_resume_selector()`
- `cmd_up()` returns 0 (clean exit, no error)

## Testing

### Manual Testing

1. Create multiple sessions in same directory:
   ```bash
   ccb up codex
   # Do some work, exit
   ccb up codex
   # Do different work, exit
   ```

2. Test resume selector:
   ```bash
   ccb up -C dpsk -r
   ```

3. Verify:
   - Session list shows all historical sessions
   - Search filters correctly
   - Detail view shows user messages
   - Enter resumes correct session
   - Esc cancels without error

### Edge Cases

- No sessions exist: Show "No sessions found" message
- Only one session: Still show TUI (consistent UX)
- Session with no JSONL file: Show "No conversation data" in detail view
- Very long user messages: Truncate in list view, show full in detail view
- Active session (not ended): Mark with different color/icon

## Future Enhancements

- [ ] Add session tags/labels
- [ ] Show session duration
- [ ] Export session list to JSON/CSV
- [ ] Keyboard shortcuts for common actions (delete session, rename, etc.)
- [ ] Preview pane showing assistant responses
- [ ] Integration with EnhanceClaudeSearch for full-text search
