---
name: jira-bugfix
description: Use when fixing a bug that originated from a Jira ticket — reproduce first, add a regression test, keep the change minimal, never push.
---

# Jira Bugfix Workflow

1. Reproduce the failure described in the ticket before changing anything.
2. Write a failing test that captures the bug.
3. Make the smallest change that turns the test green.
4. Run the full test suite.
5. Summarize root cause + fix in a comment for the ticket. Do NOT push or open a PR.
