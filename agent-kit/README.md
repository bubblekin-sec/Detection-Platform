# Agent workflow kit

This repo's engineering workflow for coding assistants in VS Code, packaged so you can copy it into another project. It has two versions of the same instructions:

| Folder | For | What VS Code reads |
| --- | --- | --- |
| `claude-code/` | The **Claude Code for VS Code** extension (Anthropic) | `CLAUDE.md`, `.claude/rules/`, `.claude/agents/`, `.claude/skills/` |
| `copilot/` | **GitHub Copilot** chat in VS Code | `.github/copilot-instructions.md`, `.github/agents/*.agent.md`, `.github/skills/` |

Install the one your team uses. If you install both, Copilot may also pick up the Claude Code copies, because VS Code can read `.claude/skills`, `.claude/agents` and `CLAUDE.md` too. You could then see each skill twice, and Copilot might pick the Claude wording. Install both only if people on the repo use both assistants.

## What's in it

| Piece | Claude Code | Copilot | Job |
| --- | --- | --- | --- |
| Project context | `CLAUDE.md` | top of `copilot-instructions.md` | Facts about the project and where its verified commands live. A template: replace it with your project's facts. |
| Workflow rules | `.claude/rules/engineering-workflow.md`, `ui-design.md` | rest of `copilot-instructions.md` | Which workflow to use when; the main agent edits and runs checks. Always loaded. |
| `plan-change` skill | `.claude/skills/` | `.github/skills/` | Builds a brief and hands it to `change-planner`. |
| `build-slice` skill | | | Implements one observable behavior at a time, test first. |
| `review-change` skill | | | Collects the actual diff and hands it to `change-reviewer`. |
| `check-portability` skill | | | Checks a runtime boundary for laptop vs Azure Container Apps. |
| `design-ui` skill | | | Designs or reviews a screen around the user's task. |
| `change-planner`, `change-reviewer`, `ui-designer` | `.claude/agents/*.md` | `.github/agents/*.agent.md` | Read-only subagents (read and search tools only). |

Type `/plan-change`, `/build-slice`, `/review-change`, `/check-portability` or `/design-ui` in chat to run a skill. Both assistants also pick a skill on their own when your request matches its description.

## Install

Copy this `agent-kit` folder anywhere, then from inside it run the installer against your project. It needs Python 3 and copies only files that are missing.

**Windows PowerShell**

```powershell
py -3 .\kit.py install "C:\src\my-project" --target copilot --dry-run
py -3 .\kit.py install "C:\src\my-project" --target copilot
```

**macOS or Linux**

```sh
python3 kit.py install "$HOME/src/my-project" --target claude-code --dry-run
python3 kit.py install "$HOME/src/my-project" --target claude-code
```

`--target` is required: `claude-code`, `copilot` or `both`. The installer copies only the kit's Markdown files and prints one line per file:

- `ADD`: will be copied.
- `SAME`: already there and identical.
- `KEEP`: your existing `CLAUDE.md` stays. The workflow loads from `.claude/rules/`, so nothing is lost.
- `CONFLICT`: a different file is already there, or the folder is a link that leads outside the project. Nothing is copied until you merge it by hand. The usual case is an existing `.github/copilot-instructions.md`: paste the kit's **Engineering workflow** and **UI design** sections into it, then run the installer again.

If the installer stops partway (for example on a permissions error), fix the cause and run it again. Files it already copied show as `SAME`.

**Manual install:** copy the contents of `claude-code/` or `copilot/` into the project root, including the hidden `.claude` or `.github` folder. Copy only files that don't exist yet. For an existing `CLAUDE.md` or `copilot-instructions.md`, merge the kit's sections in by hand.

After installing:

1. Fill in the project context: `CLAUDE.md` for Claude Code, the top of `.github/copilot-instructions.md` for Copilot. Record the build, test and run commands once you've seen them work.
2. Install the optional `interface-design` skill (below) if you'll do UI work.
3. Commit the files so everyone on the repo gets the same workflow.

### The interface-design skill

`design-ui` and `ui-designer` use a third-party skill, `interface-design` (github.com/Dammyjay93/interface-design). It isn't copied into this kit because it has its own source and terms. Install it from the project root if your organisation allows it:

- Claude Code: `npx skills add Dammyjay93/interface-design --skill interface-design --agent claude-code`
- Copilot: `npx skills add Dammyjay93/interface-design --skill interface-design`, and choose GitHub Copilot as the agent.

Without it, `design-ui` still works with the project's existing components.

## Check it loaded

**Claude Code:** open the Claude Code panel in the project root and start a new conversation. Type `/` and look for `plan-change`. Ask `/plan-change` for a small plan; the activity should show `change-planner`, and `git status` should show no edits.

**Copilot:** open Copilot Chat in Agent mode. Type `/` and look for the skills. The agent picker should list `change-planner`, `change-reviewer` and `ui-designer`. Ask `/plan-change` for a small plan and check it delegates to `change-planner`. If it says it planned on its own instead, enable the subagent tool (`agent`) in the chat's tool picker.

If something is missing:

- Check the folder sits directly under the project root you opened, spelled exactly as above.
- A skill's folder name must match the `name:` in its `SKILL.md`. Copilot ignores the skill silently otherwise.
- Organisation or repository policy can turn off custom agents or skills. Ask whoever manages Copilot or Claude Code settings.

## How the two versions differ

The text is the same workflow. The Copilot copy changes only what Copilot needs:

- Agents are `*.agent.md` files with `tools: ['read', 'search']` (Claude Code: `tools: Read, Glob, Grep`). `ui-designer` lists its skills in a `skills:` field.
- Skills read the request from the chat instead of Claude Code's `$ARGUMENTS` placeholder.
- Paths point at `.github/` instead of `.claude/`, and "main session" reads "main agent".
- The two rules files are merged into `copilot-instructions.md`, which Copilot always loads.

Copilot's subagent and skill features change quickly. If a field stops working, check the current VS Code docs: [custom instructions](https://code.visualstudio.com/docs/copilot/customization/custom-instructions), [custom agents](https://code.visualstudio.com/docs/copilot/customization/custom-agents), [agent skills](https://code.visualstudio.com/docs/copilot/customization/agent-skills).

## Keeping the kit current (this repo only)

This repo's own `.claude/` folder is the source. After changing a skill, agent or rule there:

```powershell
py -3 agent-kit/kit.py refresh          # copy .claude/ into claude-code/
py -3 agent-kit/kit.py refresh --check  # exit 1 on drift
```

On macOS or Linux, use `python3` instead of `py -3`.

Then make the same change in the matching `copilot/` file by hand. `refresh --check` also fails when a skill or agent exists on only one side, or when a `name`, `description` or `argument-hint` differs between the two versions. It doesn't compare the body text.

- `CLAUDE.md` in the kit is a placeholder template, not this repo's `CLAUDE.md`.
- When you work inside this repo, Claude Code also lists the kit's skills as folder-scoped copies (for example `agent-kit/claude-code:plan-change`). That's expected and harmless: they apply only to files under `agent-kit/claude-code/`.
