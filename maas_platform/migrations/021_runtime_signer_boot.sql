-- Existing runtimes use their original per-GPU host signer.
ALTER TABLE execution_runtime ADD COLUMN signer_boot text
    CHECK (signer_boot IS NULL OR signer_boot = identity->>'boot');
