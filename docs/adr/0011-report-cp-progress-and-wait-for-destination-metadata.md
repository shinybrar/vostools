# Report `cp` progress and wait for destination metadata

> **Partially supersedes
> [Release fsspec-cli independently inside the vosfs workspace](./0001-release-fsspec-cli-independently.md):**
> only its list of runtime dependencies is replaced; `fss-cli` now also
> requires `tqdm`. Neither distribution depends on the other at runtime.

Status: Accepted

A file `cp` printed nothing until it finished, and a destination whose `_info`
lagged behind a successful write failed with one generic
`verification failure` line naming the typed operand. Users could not tell a
slow transfer from a stuck one, or a metadata race from a real disagreement.

## Decision

Draw one byte progress bar per transfer with `fsspec.callbacks.TqdmCallback`
on stderr, using tqdm's `disable=None` so the bar appears only when stderr is
a terminal. Cross-source `cp` passes one bar to `_get_file` and another to
`_put_file`; same-source `cp` passes one to `_cp_file` only when that hook
declares a `callback` parameter. `tqdm` is a required runtime dependency,
because the bar is part of ordinary terminal use and an optional extra would
either fail mid-copy or silently drop it.

After each file is written, print the resolved destination and byte count,
then poll destination `_info` every 5 seconds for at most `--metadata-wait`
seconds (default 60) while it still reports no size, the size it had before
the copy, or, at the right size, the shared tokens it had before the copy.
Any other disagreement is reported at once and never retried into success.
The command never deletes the destination and never says the bytes are bad.

## Consequences

- `cp` success is no longer silent: each file writes two stderr lines. Stdout
  stays empty.
- Status `0` still requires a destination file whose size equals the frozen
  source size and whose shared tokens match. A run that ends while metadata
  is still stale exits `1` with `not yet verified`.
- A backend that never calls the callback shows no bar; the outcome lines
  still print.
- `cp -R` and `mv` keep their existing verification.

Normative detail belongs in `libs/vosfs/docs/design/fss-cli/commands.md`.
