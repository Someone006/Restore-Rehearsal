# Supported evidence formats

Use SARIF 2.1 JSON for static findings; CycloneDX or SPDX JSON for components and licenses; JUnit
XML for suite/case status; Cobertura or LCOV for coverage summaries; OpenAPI JSON/YAML for local
contracts; user-generated Terraform JSON or static `.tf` structure; Kubernetes YAML for structural
workload relationships; and normalized CI-result JSON for jobs and checks.

Format recognition is not authenticity. Record the producer and digest separately.
