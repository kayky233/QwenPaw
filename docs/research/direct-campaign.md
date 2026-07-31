# Direct AutoResearch Issue Campaign

The direct Campaign workflow turns one GitHub Issue into a bounded,
host-verified implementation run. It uses separate Implementer and Reviewer
agents, an isolated Git worktree, explicit file scope, real validation commands,
two evidence gates, and a durable report.

The default delivery mode is local. It creates a verified commit in the
Campaign worktree but does not push anything. Remote delivery must be selected
explicitly and creates a Draft Pull Request only. Auto-merge is not available.

## 1. Prepare QwenPaw

Enable the existing local execution acknowledgement before starting QwenPaw:

```bash
export QWENPAW_UNSAFE_RESEARCH=1
qwenpaw app
```

Create the two isolated Campaign agents once:

```bash
qwenpaw campaign-setup \
  --implementer implementer \
  --reviewer reviewer
```

The setup command creates only missing profiles, never overwrites existing
agents, gives each role an independent workspace, and reuses the globally
selected model when no model override is supplied.

Check the complete runtime before starting:

```bash
qwenpaw campaign doctor \
  --implementer implementer \
  --reviewer reviewer
```

For Draft PR delivery, also authenticate the GitHub CLI and run:

```bash
gh auth status
qwenpaw campaign doctor \
  --delivery draft_pr \
  --implementer implementer \
  --reviewer reviewer
```

## 2. Run directly from one command

```bash
qwenpaw campaign run \
  https://github.com/owner/repository/issues/123 \
  --allow src/package/fix.py \
  --allow tests/test_fix.py \
  --accept "The reported regression is fixed" \
  --accept "Existing behavior remains compatible" \
  --check "pytest -q tests/test_fix.py" \
  --implementer implementer \
  --reviewer reviewer \
  --delivery local \
  --report campaign-result.json
```

The command waits for completion and prints each Campaign phase. It writes:

```text
campaign-result.json
campaign-result.md
```

The final report contains the worktree, branch, commit, delivery receipt,
verified artifacts, CI/review lifecycle when applicable, and any blocking
error.

`--allow` and `--check` remain explicit by design. Automatically granting file
scope or executing model-generated commands would weaken the Campaign security
boundary. Every writable path and validation command must therefore be approved
by the operator.

## 3. Reuse a Campaign manifest

Create a manifest:

```bash
qwenpaw campaign init \
  https://github.com/owner/repository/issues/123 \
  --allow src/package/fix.py \
  --allow tests/test_fix.py \
  --accept "The reported regression is fixed" \
  --check "pytest -q tests/test_fix.py" \
  --implementer implementer \
  --reviewer reviewer \
  --output campaign.json
```

Run it repeatedly:

```bash
qwenpaw campaign run --config campaign.json
```

A generated manifest may be incomplete when no `--allow` or `--check` options
are supplied. Edit `modifiable_files` and `commands` before running it. This is
intentional: Campaign scope and validation must be explicitly approved.

A complete example is available at:

```text
docs/research/campaign.example.json
```

## 4. Disconnect, reconnect, list, or cancel

The server persists Campaign state. The CLI can disconnect without terminating
the task.

Read one state:

```bash
qwenpaw campaign status CAMPAIGN_ID
```

Reconnect, continue waiting, replay events, and regenerate the report:

```bash
qwenpaw campaign-watch CAMPAIGN_ID \
  --report recovered-result.json
```

List recent owner-scoped runs:

```bash
qwenpaw campaign-history
qwenpaw campaign-history --status failed --limit 50
```

Cancel an active run:

```bash
qwenpaw campaign cancel CAMPAIGN_ID
```

The status command can also regenerate a report without waiting:

```bash
qwenpaw campaign status CAMPAIGN_ID --report current-result.json
```

## 5. Use a verified local result

Local delivery preserves the Campaign worktree and writes a verified Patch
artifact. Apply it to a clean checkout of the same repository:

```bash
qwenpaw campaign-local apply CAMPAIGN_ID \
  --repository . \
  --confirm APPLY_AUTORESEARCH_PATCH
```

Before applying, the command checks:

- the Campaign completed in local mode;
- the target origin is the Issue repository or a same-name GitHub Fork;
- the target repository is clean;
- the target HEAD matches the verified parent revision;
- the Patch exists;
- every changed path is in the approved scope;
- `git apply --check --index` succeeds.

The command stages the verified changes. It does not commit them. Review the
staged diff and commit normally.

After the result has been applied or is no longer needed, remove only the
Campaign worktree and temporary branch:

```bash
qwenpaw campaign-local cleanup CAMPAIGN_ID \
  --confirm CLEANUP_AUTORESEARCH_LOCAL
```

## 6. Create a Draft Pull Request

Select remote delivery explicitly:

```bash
qwenpaw campaign run \
  https://github.com/upstream/repository/issues/123 \
  --allow src/package/fix.py \
  --allow tests/test_fix.py \
  --check "pytest -q tests/test_fix.py" \
  --implementer implementer \
  --reviewer reviewer \
  --delivery draft_pr
```

This mode performs the same host verification, then commits, pushes the
Campaign branch, and creates a Draft Pull Request. It does not merge the Pull
Request and does not enable auto-merge.

Both normal repository and Fork workflows are supported:

```text
Issue repository: upstream/repository
origin:           upstream/repository  -> head autoresearch/issue-...
origin:           my-user/repository   -> head my-user:autoresearch/issue-...
```

A Fork must have the same repository name as the Issue repository. The branch
is pushed only to the resolved `origin`; the Draft Pull Request target remains
the Issue repository. The delivery receipt verifies both Commit SHA and exact
PR Head identity.

The command monitors the verified PR head, CI checks, and review decision. The
report records one of:

```text
ci_waiting
review_waiting
needs_revision
merge_ready
monitor_unavailable
```

`merge_ready` is evidence only. The Pull Request remains Draft unless a human
uses the separately guarded promotion command, and no AutoResearch module
contains a merge operation.

If CI was still pending when the initial monitoring window ended, refresh it
without modifying the PR:

```bash
qwenpaw campaign-refresh CAMPAIGN_ID \
  --report campaign-result.json
```

The separate `remote-e2e`, `promote-ready`, and `cleanup-e2e` commands remain
available for dedicated test-repository validation. They are not required for
normal direct Campaign use.

## Campaign phases

A normal local run emits phases such as:

```text
accepted
preparing_worktree
loading_issue
running
implementing
reviewing
reviewed
delivered
```

Draft PR delivery may additionally emit:

```text
ci_waiting
review_waiting
needs_revision
merge_ready
monitor_unavailable
```

Terminal failure states are:

```text
needs_revision
blocked
failed
cancelled
```

A non-delivered result still writes a report so the exact failed command,
review finding, scope violation, missing evidence, CI failure, or review blocker
can be inspected.
