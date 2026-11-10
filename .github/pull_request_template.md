## What changed

Describe the user-visible behavior and why it belongs in Rehearse.

## Verification

- [ ] Formatting and lint pass.
- [ ] Relevant unit/adversarial tests pass.
- [ ] Package and installed-wheel smoke tests pass when packaging changed.
- [ ] The real Docker integration test passes when restore/isolation behavior changed.
- [ ] Documentation, changelog, compatibility notes, and bundled-skill mirrors are updated.

## Safety and privacy

- [ ] No credential, API key, private key, backup, personal/customer data, absolute personal path,
      or private project content is included.
- [ ] New parsing and graph behavior is bounded and rejects unsafe/unknown input.
- [ ] New network or execution behavior is explicit, documented, and opt-in.
- [ ] Generated reports and examples contain only synthetic data.

## Compatibility

List schema, CLI, platform, dependency, plugin, recipe, or report compatibility effects. Automated
findings must not be described as security, legal, accessibility, or recovery certification.
