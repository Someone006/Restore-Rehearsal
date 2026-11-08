# Performance and Operability

Lens ID: `performance-operability`

Review service health, timeouts/retries, resource boundaries, queues, dependency fan-out, runbooks,
and logs/metrics/traces evidence. Flag unbounded behavior and critical paths without operational
signals, while clearly marking pattern-based observations as heuristic.

Propose measurable latency/resource budgets, load or soak tests, retry-budget tests, failure-mode
checks, and telemetry assertions. Do not predict performance from source topology alone.
