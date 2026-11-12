"""Resolve v2 replica costs from current native declarations, never old policy hints."""
from dataclasses import replace


class ResourceDeclarationError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def positive_bytes(value):
    if type(value) is int and 0 < value <= 2**63 - 1:
        return value
    if (isinstance(value, str) and 1 <= len(value) <= 19 and value.isascii()
            and value.isdecimal() and 0 < int(value) <= 2**63 - 1):
        return int(value)
    raise ResourceDeclarationError('resource_claim_invalid')


class RuntimePolicy:
    """Snapshot-local overlay; original policy/revision and hot-reload identity stay intact."""
    def __init__(self, policy, claims):
        self.original, self.claims = policy, claims
        self.models = {key: {**fields, 'vram_claim_bytes': claims[key]}
                       for key, fields in policy.models.items()}

    def effective(self, event):
        rules, execution = self.original.effective(event)
        return {key: replace(rule, vram_claim_bytes=self.claims[key]) for key, rule in rules.items()}, execution

    def __getattr__(self, name):
        return getattr(self.original, name)


def resolve_resources(policy, models):
    if isinstance(policy, RuntimePolicy):
        return policy
    if policy.schema_version != 2:
        raise ValueError('Live resource declarations require v2')
    rows = {row.id: row for row in models if row.id in policy.models}
    if rows.keys() != policy.models.keys():
        raise ValueError('Managed deployment missing from snapshot')
    claims = {}
    for key, row in rows.items():
        env = row.env or {}
        if not isinstance(env, dict) or 'GPUSTACK_MODEL_VRAM_CLAIM' not in env:
            raise ResourceDeclarationError('resource_claim_missing')
        value = env['GPUSTACK_MODEL_VRAM_CLAIM']
        if not isinstance(value, str):
            raise ResourceDeclarationError('resource_claim_invalid')
        claims[key] = positive_bytes(value)
    return RuntimePolicy(policy, claims)
