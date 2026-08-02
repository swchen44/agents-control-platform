"""ARCP B-phase harness: Jira outer loop (v5 design, route-B execution).

Layering rule (v5 D6b + B-phase survival rules): everything upstream consumes
the normalized Ticket model only; Jira Cloud specifics live in jira_source.py
alone, so moving to Jira Server/DC swaps exactly one file.
"""
