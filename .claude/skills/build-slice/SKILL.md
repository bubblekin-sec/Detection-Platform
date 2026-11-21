---
name: build-slice
description: Implement a feature or bug fix through its normal entry point, one observable behavior at a time. Use for coding requests that need a focused test and verified implementation.
argument-hint: "[behavior or planned increment to implement]"
---

Implement the requested scope in the main session. The supplied detail is $ARGUMENTS

1. Use the existing plan when one exists. Otherwise state the observable result in one sentence, find the closest implementation and tests, and choose the smallest complete path through the affected layers. For a consequential unresolved design choice, use `plan-change`. A request for one increment ends after that increment. A request for the whole feature may take several.
2. For a bug, reproduce it through a public boundary and capture it in a failing regression test. For new behavior, add one test that would fail if the behavior were absent. Run the focused test and inspect the failure reason. For a behavior-preserving refactor, start with passing characterization coverage. For presentation-only work, use the appropriate render, build, or manual check.
3. Implement just enough for that example. Keep authoritative validation with its existing owner. Reuse the repo's config and client construction. If a real external dependency enters the path, use `check-portability` for that boundary and substitute the I/O dependency in local tests. A callable or FastAPI dependency is often sufficient.
4. Rerun the focused check. When it passes, simplify the changed code and run it again if you refactored. Repeat for further behavior in scope. Preserve error handling, accessibility, and compatibility that callers rely on.
5. Verify the changed surfaces using commands from the manifests or CI. Select evidence for the surfaces in the diff.

   | Surface | Example of evidence |
   | --- | --- |
   | FastAPI endpoint | Use the repo's HTTP test client. Exercise a successful request and the meaningful failing request. Assert status and response fields visible to the caller. |
   | React interaction | Use the existing component or browser runner. Interact by accessible role or label and check the result or error the user sees. |
   | Process or service boundary | Exercise the actual serialization, proxy path, or database behavior at issue. A local fake can exercise failure handling. Provider-specific claims need a check against a compatible service. |

   If the repo is new, establish the smallest runnable test for the first feature. If checks already exist, use the relevant lint, type, build, and test commands. Report an unavailable runner or baseline failure explicitly.
6. Use `review-change` for a changed public contract, authorization, persistence, or service boundary, or when the user asks. Pass the acceptance criteria, changed paths, and any known pre-existing user edits so the review stays focused on this work. During a build, correct substantiated findings within scope and rerun the affected checks. Finish with the behavior delivered, commands and results, and any remaining verification gap.
