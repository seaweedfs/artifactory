# QA Bundle Assert

`scripts/qa-assert.sh` is the shared acceptance check for dashboard bundles. Use
it after a scenario finishes and before calling a run ACCEPT.

It checks the common envelope:

- `result.json` says PASS;
- `status.json` says pass;
- `__gate_pass=1`;
- `__tested_ref` matches the requested ref;
- `__tested_sha` is present;
- `__lab_run_id` is present.

Then it applies one product profile, such as RDMA or block.

## RDMA

```bash
scripts/qa-assert.sh \
  /mnt/smb/work/share/testops/results/rdma-ci/<run-id> \
  --ref <branch-or-sha> \
  --profile docs/qa-profiles/rdma.expect
```

The RDMA profile currently checks the RC/DC push floors and basic loader rows.

## Block

```bash
scripts/qa-assert.sh \
  /mnt/smb/work/share/testops/results/block-ci/<run-id> \
  --ref <branch-or-sha> \
  --profile docs/qa-profiles/block.expect
```

The block profile is intentionally sparse until block emits the common envelope.
Add block-specific rows there when the unified block gate stabilizes.

## Output

Success prints:

```text
QA_BUNDLE_ASSERT_OK
product=rdma
tested_ref=<branch-or-sha>
tested_sha=<resolved-sha>
lab_run_id=<runner-id>
...
```

Failure exits non-zero and prints the first missing or mismatched field. Do not
soften that failure in a QA verdict; either fix the gate or record it as not
accepted.
