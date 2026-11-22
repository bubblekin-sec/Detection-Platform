# Project context

<!-- Replace this section with your project's facts: what it is, who uses it, and where it deploys. -->
Describe the project and its deployment target here, for example a React UI served by nginx and a FastAPI backend, each in its own Azure Container App. Check the repository before describing any part of that target as implemented.

Use synthetic examples and developer-controlled dependencies for local work. Introduce SQL, messaging, or an external API when a feature actually needs it. Keep the application and its tests runnable with ordinary project commands.

## Working commands

Use the package manifests, lockfiles, contributor guide, and CI configuration to find the supported commands and their working directories. Once verified, record them here or link to the existing authoritative guide. On an empty repo, record the first working build and test commands as part of the first feature.

# Engineering workflow

Match the work to the request. Answer design questions directly. Make a small, obvious correction yourself and run the relevant check. Use these project skills for substantial changes or when the user requests the workflow by name.

| When | Use |
| --- | --- |
| Planning a feature, changing a contract, or choosing between consequential designs | `plan-change` |
| Implementing a feature or fixing a behavioral bug | `build-slice` |
| Reviewing a diff or finishing a change to a public contract, persisted data, authorization, or a service boundary | `review-change` |
| Introducing or changing a runtime setting, identity, network route, external dependency, or container behavior | `check-portability` |
| Planning, building, or reviewing a change to what people see or do on a screen | `design-ui` (see UI design below; consult `ui-designer` for a new multi-screen flow or a consequential layout or navigation choice) |

The main agent owns edits and test execution. Give the planner, designer, and reviewer enough context to work independently. Honor plan-only and review-only requests.

- Trace the entry point, its callers, and an existing example before creating anything. Reuse the existing owner of a rule or representation. Extract shared code when consumers need the same behavior and will change for the same reason.
- Preserve observable contracts unless the user requests a change. Consider earlier data, error paths, and callers outside the immediate module when the diff affects them.
- Prefer a direct function or the framework's existing facility. Justify a new dependency or layer by the specific behavior or integration it enables today.
- Keep tests focused on outcomes a caller can observe. Use the repo's checks and distinguish an observed pass, an existing failure, and an unrun check in the handoff.

## UI design

When planning, building, or reviewing a change that affects what people see or do on a screen, apply the project skill `design-ui`. If your available tools only allow reading, read `.github/skills/design-ui/SKILL.md` directly.

For routine changes, apply the skill in the current chat. For a new multi-screen flow or a consequential layout or navigation choice, the main agent consults the read-only `ui-designer` with the user goal, relevant files, and constraints. Reuse its brief in planning, implementation, and review. If the specialist is unavailable, use the skill directly. The main agent owns edits and runs the checks.
