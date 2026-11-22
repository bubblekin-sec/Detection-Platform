---
name: review-change
description: Review working changes, a branch against a named base, or an explicit patch for correctness, regression risk, redundant logic, and meaningful test coverage.
argument-hint: "[working changes, staged only, or branch and base]"
---

Review the supplied scope. The supplied detail is the text after the slash command, or the user's request when the skill was loaded automatically.

The main agent collects evidence under its normal tool permissions. Identify the requested behavior and acceptance criteria. With no scope specified, review uncommitted changes, including staged, unstaged, and new in-scope source or test files.

| Scope | Evidence to give the reviewer |
| --- | --- |
| Working changes | Run `git status --short --untracked-files=all`. Inspect the staged patch with `git diff --cached` and the unstaged patch with `git diff`. Read relevant untracked source and tests separately. Distinguish the two patches when a file has both kinds of edits. |
| Staged only | Inspect `git diff --cached`. Use the index version for context if the working copy differs. |
| Branch or PR | Resolve the requested base and head to locally available refs. Verify the merge base, then review `git diff <base>...<head>` and its paths. State the compared refs and whether any local edits were also requested. |
| Supplied patch or project outside Git | Use the supplied patch or explicit files and requirements. Label a files-only review as a snapshot and identify missing history. |

Use literal, verified refs and paths in commands. If a base is unavailable, ask for the intended ref or patch. When a working-tree review has no changes, report the empty scope. Start with filenames. Exclude credential material, generated binaries, and unrelated user work from the handoff and state any relevant exclusions.

Delegate to the custom agent `change-reviewer` as a subagent. Pass the **actual diff**, new file contents or readable paths, acceptance criteria, and any exact test results. For a staged or branch review, provide version-specific excerpts when the working files differ. Break a large diff into coherent groups and account for each one. Give the reviewer enough evidence to form its own conclusion.

Return the findings and their locations before the summary. If subagents are unavailable, apply `.github/agents/change-reviewer.agent.md` yourself and identify the review as a self-review. A review-only request ends with the findings.
