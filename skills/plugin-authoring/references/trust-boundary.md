# Plugin trust boundary

Listing installed entry-point metadata must not import its module. Activation is by explicit ID and
means trusting ordinary Python code with the user's filesystem, process, environment, and network
privileges. Rehearse core does not claim to sandbox it.

Never auto-enable a plugin because a project file requests it. Show package/distribution identity,
version, capability, and requested action before execution.
