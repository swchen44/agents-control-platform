"""ARCP PoC — a ~600-line cross-CLI supervisor for headless coding agents.

Modules:
  events     unified AgentEvent schema + RunState machine (the cross-CLI layer)
  drivers    claude -p / codex exec raw-subprocess adapters (+ OpenHands ACP note)
  supervisor spawn/trace/state-machine/watchdog/control, with live + replay modes
  rules      JSON rule engine (assignee/keyword -> agent/skills/repo)
  workspace  per-issue folder + AgentSkills provisioning
  jira_watcher  poll Jira Server, match rules, dispatch supervised runs
"""
