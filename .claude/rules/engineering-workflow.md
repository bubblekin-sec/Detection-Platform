# Engineering workflow

Match the work to the request. Answer design questions directly. Make a small, obvious correction in the main session and run the relevant check. Use these project skills for substantial changes or when the user requests the workflow by name.

| When | Use |
| --- | --- |
| Planning a feature, changing a contract, or choosing between consequential designs | `plan-change` |
| Implementing a feature or fixing a behavioral bug | `build-slice` |
| Reviewing a diff or finishing a change to a public contract, persisted data, authorization, or a service boundary | `review-change` |
| Introducing or changing a runtime setting, identity, network route, external dependency, or container behavior | `check-portability` |

The main session owns edits and test execution. Give the planner and reviewer enough context to work independently. Honor plan-only and review-only requests.

- Trace the entry point, its callers, and an existing example before creating anything. Reuse the existing owner of a rule or representation. Extract shared code when consumers need the same behavior and will change for the same reason.
- Preserve observable contracts unless the user requests a change. Consider earlier data, error paths, and callers outside the immediate module when the diff affects them.
- Prefer a direct function or the framework's existing facility. Justify a new dependency or layer by the specific behavior or integration it enables today.
- Keep tests focused on outcomes a caller can observe. Use the repo's checks and distinguish an observed pass, an existing failure, and an unrun check in the handoff.
