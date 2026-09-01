<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Cost report — Portable AI Gateway productization

Status: `inconclusive` (local source-validation phase executed)

## Current phase

| Cost class | Measured | Estimated ceiling | Basis |
| --- | ---: | ---: | --- |
| External/cloud services | EUR 0 | EUR 0 | No external resource or paid inference is authorized for the local phase. |
| Local compute/electricity | Not measured | Not estimated | Existing workstation; short source tests/static checks only, with no accepted benchmark window. |
| Personnel/time | Not measured | Not included | Outside the technical PoC cost threshold. |
| Artifact storage/registry | EUR 0 | EUR 0 | No publication or external registry push is authorized. |

No billable action was executed or inferred. EUR 0 describes recorded external cost for the executed local source gate, not local electricity, personnel effort, total cost of ownership or a future deployment estimate.

## Future GCP gate

Before `terraform apply`, cloud inference, image push, or managed-service creation:

1. select project, region, services, retention, minimum/maximum scaling, and test duration;
2. capture a dated provider estimate for Cloud Run, Cloud SQL, cache, registry/build, logging, network, and Vertex usage;
3. add contingency and define a maximum EUR stop threshold;
4. obtain explicit approval for credentials, resources, deployment, inference, and cost;
5. tag every temporary resource for attribution/cleanup and capture measured billing where available.

A local result cannot estimate production cost or prove that a managed-service configuration stays within a future budget.
