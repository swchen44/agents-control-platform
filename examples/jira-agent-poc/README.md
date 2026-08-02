# jira-agent-poc

A ~600-line, zero-dependency proof-of-concept for the **cross-CLI supervisor**
layer described in `research/2026-08-agent-runtime-control-plane-research-v3.md`.

It demonstrates the whole target pipeline end to end:

```
Jira issue ─▶ rule engine (assignee/keyword JSON) ─▶ provision workspace + install skills
          ─▶ supervise a headless run (claude -p / codex exec) ─▶ unified trace + control
```

The point it proves: **one normalized event schema + state machine works
identically over `claude -p` and `codex exec`**, giving you trace and control
that neither raw CLI nor "OpenHands-for-ACP" gives you for free.

## Layout

| file | what |
|---|---|
| `arcp_poc/events.py` | unified `AgentEvent` schema + `RunState` machine (the cross-CLI layer) |
| `arcp_poc/drivers.py` | `claude -p` / `codex exec` raw-subprocess adapters; OpenHands-ACP path documented |
| `arcp_poc/supervisor.py` | spawn / trace / state-machine / stall-watchdog / control (pause, resume, kill); live + replay modes |
| `arcp_poc/grader.py` | evidence-based stop: deterministic graders (files / command / all-of); DONE that fails evidence is overridden to FAILED |
| `arcp_poc/resume_transcript.py` | recovery rung 2: render the journal into a bootstrap prompt for a FRESH session when native resume is unavailable |
| `arcp_poc/rules.py` | JSON rule engine (assignee / keyword → agent / skills / repo) |
| `arcp_poc/workspace.py` | per-issue folder + AgentSkills provisioning |
| `arcp_poc/jira_watcher.py` | poll Jira Server REST, match rules, dispatch |
| `rules.json` | example rules |
| `skills/jira-bugfix/SKILL.md` | example AgentSkills skill |
| `fixtures/*.jsonl` | **real** captured event streams from claude 2.1.206 / codex-cli 0.142.5 |
| `recovery_test.py` | crash-recovery matrix test: controlled kill → `--resume` → deterministic grading (report §9.3-1) |
| `permission_matrix.py` | headless permission-mode behavior matrix: 6 modes × Write/Bash probes (report §9.3-3) |

## Run

```bash
# 1. Offline — no tokens spent. Replays real captured streams through the pipeline.
python3 replay_demo.py

# 2. Self-tests — no tokens spent.
python3 selftest.py

# 3. Live — spends tokens on your claude / codex subscription (trivial prompt).
python3 run_demo.py claude
python3 run_demo.py codex "Reply with exactly the word: pong (trivial bug check)"

# 4. Live — crash-recovery matrix (claude, model=haiku, ~$0.2 total).
python3 recovery_test.py                     # full 2x2: early/midtool x SIGTERM/SIGKILL
python3 recovery_test.py --case midtool:SIGKILL
python3 recovery_test.py --resume-mode transcript --case midtool:SIGKILL
                                             # rung 2: journal -> fresh session
```

## Verified (2026-08-01, this machine)

- claude 2.1.206 · codex-cli 0.142.5 · opencode (has `opencode acp`)
- Live `claude -p` run: reached `done`, cost $0.0189, pre-assigned session id honored.
- Live `codex exec` run: rule matched `ops-bug-to-codex`, skill `jira-bugfix`
  installed into `.claude/skills/`, reached `done`.
- Offline replay + 7/7 self-tests pass.
- **Crash→resume matrix (claude): 4/4 cases PASS, 16/16 checks** — kill at
  "no output yet" / "mid-tool" × SIGTERM/SIGKILL, then `--resume <pre-assigned id>`
  reattaches the SAME session, remembers progress, does NOT redo finished steps
  (mtimes unchanged), finishes the task. Streams saved as
  `fixtures/claude_p_{crash,resume}_real.jsonl` (replay-validated).
- **Crash→resume matrix (codex): full 2x2 PASS** (midtool×SIGTERM re-measured
  2/2 clean on 2026-08-02 after the host-sleep artifacts were identified).
  The thread id harvested from `thread.started` is enough to
  `codex exec resume <id>`. Traps pinned along the way: SIGTERM → graceful
  **rc=0** (a killed run grades as DONE unless you use an evidence-based
  grader), `exec resume` rejects `--sandbox` (use `-c sandbox_mode=...`),
  kill the process GROUP or codex's shell child finishes the task as an
  orphan, and codex's tool granularity varies run to run — trust the
  filesystem, not the event stream. Streams: `fixtures/codex_exec_{crash,resume}_real.jsonl`.
- Run live experiments under `caffeinate` (and ideally on AC power): host
  sleep freezes supervisor timers and poisons stall detection.

## What this PoC deliberately does NOT do (see report §5, §10)

- Crash **recovery**: the claude resume baseline is now measured (`recovery_test.py`),
  but automatic resume-on-failure inside the supervisor, the codex path, and the
  worktree scenario (issue #48835) remain TODO.
- Control floor is honest: raw CLIs have no cooperative pause, so `pause()` uses
  SIGSTOP. OpenHands offers `POST /pause` — that trade-off is the report's §7.
- Jira watcher is polling-only; live dispatch to an agent-server (OpenHands path)
  is documented, not wired.
