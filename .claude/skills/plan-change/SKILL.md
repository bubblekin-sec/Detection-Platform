---
name: plan-change
description: Plan a feature or consequential change before editing. Use for design questions that need repository evidence, contract changes, or requests for a step-by-step implementation plan.
argument-hint: "[desired behavior and constraints]"
---

Plan the change described by the user. The supplied detail is $ARGUMENTS

1. Assemble a self-contained brief from the request and any decisions already made in the conversation. Include the observed behavior, desired outcome, constraints, and any known file paths. Separate a proposed product decision from a fact found in the repo.
2. Delegate that brief to the project subagent `change-planner`. Wait for its plan. If delegation is unavailable, perform the same read-only planning pass using the instructions in `.claude/agents/change-planner.md` and disclose that the main session did it.
3. Return the plan and its first testable increment. Keep the plan in chat unless the user requests a file or the repo has an established place for this task's plan. A planning request ends here. If the user also asked for implementation, carry the agreed constraints into the build and proceed when the next step is clear.
