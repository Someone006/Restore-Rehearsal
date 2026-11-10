# Parser safety

Enforce byte, record, nesting, string, and graph limits. Refuse XML document types and entities,
remote references, absolute or parent-traversing locations, unsafe YAML expansion, and unknown
top-level shapes. Never run Terraform, Helm, test runners, package managers, or artifact hooks as
part of import.

Discard logs, source snippets, fixes, invocation environments, authorization material, example
payloads, secret fields, and raw infrastructure values even when the format allows them.
