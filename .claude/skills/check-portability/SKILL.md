---
name: check-portability
description: Assess a changed integration or runtime boundary for local development and Azure Container Apps. Use for container startup, browser routing, external clients, identity, persistence, or messaging.
argument-hint: "[changed boundary or deployment question]"
---

Assess the boundary described by the user. The supplied detail is $ARGUMENTS

Trace the calling process, where it gets configuration, the receiving process or service, and the contract between them. Check the relevant rows below. For pure application logic, a sentence identifying its inputs and outputs is enough. This skill produces an assessment. Provisioning and deployment are separate tasks.

| When the change touches | What to examine and how to prove it |
| --- | --- |
| React → nginx → FastAPI | Prefer a same-origin browser path such as `/api` when nginx fronts an internal API. Configure nginx's upstream at container startup. Trace the path the API actually receives, including any prefix rewrite. A Vite-built bundle embeds its build-time values. Keep service-only hostnames and credentials out of browser assets. Smoke-test one request through nginx with a configurable local upstream. |
| Configuration and SDK clients | Read required settings at the process entry point or existing settings module. Construct the real client there and pass the narrow operation the feature uses. Local tests can supply a fake or disposable service. A missing required production setting should produce an actionable, redacted startup or readiness failure. |
| Identity and credentials | Keep the caller's authorization in the API. A container's managed identity identifies the workload when accessing a compatible Azure service. Identify which workload needs which access. For an external service such as GitHub, configure its supported credential at the server boundary. Keep actual values out of source, images, fixtures, and logs. Use a Container Apps secret reference or Key Vault reference when a secret is required. |
| State and messages | Keep durable work outside container-local disks and process memory. For SQL changes, check the target dialect, constraints, migration compatibility, and concurrent writes when applicable. For a Service Bus consumer, test the same message arriving twice and a failure between saving the result and acknowledging it. Preserve the business effect across retries. |
| Container lifecycle | For an HTTP service, listen on a container-reachable address and match the configured ingress target port. Give liveness a process-local check and use readiness for dependencies needed to serve traffic. For a slow start, inspect startup probe timing. Send operational logs to stdout or stderr with enough context to trace a failed request. Confirm restart behavior for changed state. |

Cover three points for each consequential issue.

1. The file or setting and what would fail when the environment changes.
2. The smallest change needed now, or the specific enterprise input needed at deployment (for example an upstream hostname, identity assignment, or database role).
3. A local check and, when relevant, an enterprise-only check. Mark each as observed, proposed, or blocked. Distinguish a simulated response from a real service check.

When existing code already handles the boundary, point to the evidence and keep the design intact. Add a provider interface only when a real implementation or test needs substitution at that location.
