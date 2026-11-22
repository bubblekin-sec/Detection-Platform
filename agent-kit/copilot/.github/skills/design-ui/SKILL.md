---
name: design-ui
description: Design and review screens for internal workflow applications. Use for user-facing changes involving page purpose, layout, navigation, information density, editing, feedback, or work that crosses screens or tools.
---

# Design the screen around the work

## Understand the task

Read the request, the affected flow, and the closest comparable screen. Identify who arrives, what brought them here, what they need to decide or finish, and where they go next. An overview can succeed when the user can tell everything is healthy. For a new screen, state any assumption that would change the design.

Trace one realistic task. Find the moment someone opens another product, copies a value, or loses their place. Bring the specific fact or supported action they need into the workspace. Link to the original record for deeper inspection. Flag missing data or capabilities before presenting a design that depends on them.

## Choose what belongs on screen

Group facts that answer the same question, even when they come from different systems. Keep information that changes a decision in view beside the relevant action. Reveal occasional details on demand. Show source or freshness when either affects trust in the result.

Use the task and the shape of the data to choose a layout. These are starting points.

| What people need to do | A layout to consider |
| --- | --- |
| Compare or prioritize many items | Align the few distinguishing fields in a table or compact list. |
| Move work through stages | Use a board when stage and handoff determine the next move. |
| Inspect several items in succession | Keep the list in view with adjacent detail when space permits. |
| Create or edit something substantial | Give the editor room. Keep guidance, validation, and save feedback near the work. |
| Monitor conditions | Surface exceptions and their impact. Use a trend chart when change over time matters. |

Use the project's terminology. Give each panel, badge, and summary a concrete job. Preserve filters, drafts, selection, or return position where losing them would make someone repeat work.

## Apply Interface Design

For a new screen or an open visual choice, give `interface-design` the page's job, the facts that must stay visible, and the closest shared components. Apply it to hierarchy, density, typography, spacing, surfaces, and control states. For a routine use of an established component, reuse its existing treatment.

`ui-designer` lists `interface-design` in its `skills`. Outside `ui-designer`, invoke `interface-design` when an open visual choice remains. A read-only agent without that skill loaded can read its `SKILL.md` from wherever it was installed, such as `.github/skills/interface-design/` or `.agents/skills/interface-design/`. Carry the resulting decisions into implementation and review.

Read `.interface-design/system.md` when present. Use the project's actual components and theme tokens. Flag any discrepancy that affects the page. When the team adopts a pattern for future screens, the main agent can record it in that file with a pointer to the implementation. A design-only review proposes the entry in its brief.

If Interface Design is missing, continue with the existing components and give the developer the project setup command `npx skills add Dammyjay93/interface-design --skill interface-design`, choosing GitHub Copilot as the agent.

## Verify the experience

Walk through the main task and one relevant interruption, such as an empty result, unavailable evidence, or a failed save. Use representative content, including long values where relevant. Check keyboard access, visible focus, status meaning beyond color, and readability at a narrower supported width.

For design work, return a short brief with the page's purpose, the layout and why it fits, what stays visible or opens on demand, and one observable success check. For implementation, carry those decisions into the existing build workflow and run the relevant interaction or render check. For review, name the location, consequence for the user, and smallest correction. Distinguish observed behavior from a proposed check.
