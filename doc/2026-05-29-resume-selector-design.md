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

### Session File (`.codex-session*`)

```python
{
    "session_id": "ai-1716890000-12345",  # CCB session ID
    "started_at": "2026-05-29 10:47:30",
    "ended_at": "2026-05-29 11:30:15",    # None if still active
    "work_dir": "E:\\ccb",
    "providers": ["codex"],                # Extracted from file presence
    "pane_id": "123",
    "pane_title_marker": "CCB-Codex-ai-1716890000-12345"
}
```

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

Match `.codex-session` to Claude's `.jsonl` file using the existing `_claude_project_dir()` helper from `ccb`:

```python
def _find_jsonl_for_session(session_id: str, started_at: str) -> Optional[Path]:
    """Find the Claude JSONL file matching this session."""
    # Use existing CCB helper to get project directory
    project_dir = _claude_project_dir(Path.cwd())
    if not project_dir.exists():
        return None
    
    # Find JSONL files and match by modification time closest to started_at
    jsonl_files = list(project_dir.glob("*.jsonl"))
    if not jsonl_files:
        return None
    
    # Parse started_at timestamp
    try:
        from datetime import datetime
        target_time = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S").timestamp()
    except:
        return None
    
    # Find closest file by modification time (within 1 hour tolerance)
    closest = min(jsonl_files, key=lambda f: abs(f.stat().st_mtime - target_time))
    if abs(closest.stat().st_mtime - target_time) < 3600:
        return closest
    
    return None
```

**Note:** This approach uses modification time matching because CCB session files don't store the Claude session UUID. Future enhancement could add `claude_session_id` field to `.codex-session` for direct matching.

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
        """Scan directory for .codex-session* files and extract metadata."""
        sessions = []
        for session_file in sorted(self.work_dir.glob(".codex-session*")):
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
                session_id = data.get("session_id", "")
                started_at = data.get("started_at", "")
                
                # Parse providers from available session files
                providers = []
                for provider in ["codex", "gemini", "opencode"]:
                    provider_file = self.work_dir / f".{provider}-session"
                    if provider_file.exists():
                        try:
                            pdata = json.loads(provider_file.read_text(encoding="utf-8"))
                            if pdata.get("session_id") == session_id:
                                providers.append(provider)
                        except:
                            pass
                
                # Find matching JSONL file
                jsonl_path = self._find_jsonl_for_session(session_id, started_at)
                first_message = ""
                if jsonl_path:
                    messages = extract_user_messages(jsonl_path)
                    if messages:
                        first_message = messages[0][:40] + "..." if len(messages[0]) > 40 else messages[0]
                
                sessions.append({
                    "session_id": session_id,
                    "started_at": started_at,
                    "providers": providers,
                    "first_message": first_message,
                    "jsonl_path": jsonl_path,
                    "session_file": session_file,
                })
            except Exception as e:
                # Skip malformed session files
                continue
        
        # Sort by started_at descending (newest first)
        sessions.sort(key=lambda s: s["started_at"], reverse=True)
        return sessions
    
    def _find_jsonl_for_session(self, session_id: str, started_at: str) -> Optional[Path]:
        """Find the Claude JSONL file matching this session."""
        # Get project directory
        from pathlib import Path
        import hashlib
        
        project_dir_name = hashlib.sha256(str(self.work_dir).encode()).hexdigest()[:16]
        claude_projects = Path.home() / ".claude" / "projects"
        project_dir = claude_projects / project_dir_name
        
        if not project_dir.exists():
            return None
        
        # Find JSONL files and match by modification time closest to started_at
        jsonl_files = list(project_dir.glob("*.jsonl"))
        if not jsonl_files:
            return None
        
        # Parse started_at timestamp
        try:
            from datetime import datetime
            target_time = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S").timestamp()
        except:
            return None
        
        # Find closest file by modification time
        closest = min(jsonl_files, key=lambda f: abs(f.stat().st_mtime - target_time))
        
        # Only return if within 1 hour of target time
        if abs(closest.stat().st_mtime - target_time) < 3600:
            return closest
        
        return None
    
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
    # ... rest of init
```

Changes to `_start_claude()` (around line 1513):

```python
def _start_claude(self) -> int:
    # ... existing code ...
    
    if self.resume:
        if self.resume_session_id:
            # Use selected session ID from TUI
            cmd.extend(["--resume", self.resume_session_id])
            print(f"🔁 Resuming session {self.resume_session_id[:8]}")
        else:
            # Fallback to latest (existing behavior)
            _, has_history = self._get_latest_claude_session_id()
            if has_history:
                cmd.append("--continue")
    
    # ... rest of function
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
