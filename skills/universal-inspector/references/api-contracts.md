# API and contract inspection

Prefer localhost, a mock server, or an explicitly approved test endpoint. Record method, route
template, status, content type, schema result, latency range, and redacted request/response facts.
Exercise validation, authentication failure, authorization boundaries, pagination, idempotency,
rate limits, and version compatibility when in scope.

Never print or persist authorization headers, cookies, tokens, full personal records, or provider
credentials. External mutations require exact user authorization and disposable test data.
