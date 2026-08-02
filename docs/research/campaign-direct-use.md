# AutoResearch Issue Campaign — Direct Use

This is the supported operator workflow for solving a GitHub Issue with two
independent QwenPaw agents, host-verified Git evidence, bounded repairs, and an
optional Draft Pull Request. Automatic merge is not implemented.

## 1. Start QwenPaw with local execution enabled

```bash
export QWENPAW_UNSAFE_RESEARCH=1
qwenpaw app
```

The opt-in is required because the Implementer modifies an isolated Git
worktree and validation commands run on the QwenPaw host. Validation receives a
minimal environment with an isolated HOME; GitHub, model, cloud, cookie, session,
and Cargo credentials are not inherited.

## 2. Create the independent agents once

```bash
qwenpaw campaign setup-agents \
  --implementer implementer \
  --reviewer reviewer
```

The compatibility command remains available:

```bash
qwenpaw campaign-setup \
  --implementer implementer \
  --reviewer reviewer
```

The command waits until both agents report `running`. Implementer and Reviewer
must be different agent IDs and use separate workspaces.

## 3. Verify runtime readiness

Local-only delivery:

```bash
qwenpaw campaign doctor \
  --delivery local \
  --implementer implementer \
  --reviewer reviewer
```

Draft PR delivery:

```bash
qwenpaw campaign doctor \
  --delivery draft_pr \
  --implementer implementer \
  --reviewer reviewer
```

Draft PR readiness requires either:

- an authenticated `gh` CLI verified with `gh auth status`; or
- `GITHUB_TOKEN` / `GH_TOKEN` on the QwenPaw server.

No credential value is returned by the readiness endpoint.

## 4. Run a local Campaign

```bash
qwenpaw campaign run \
  https://github.com/owner/repository/issues/123 \
  --allow src/package/fix.py \
  --allow tests/test_fix.py \
  --check "pytest -q tests/test_fix.py" \
  --delivery local \
  --report campaign-result.json
```

The command performs:

1. GitHub Issue intake;
2. isolated worktree and `autoresearch/issue-*` branch creation;
3. Implementer modification inside the explicit file allowlist;
4. host-derived changed paths, binary Diff, Diff SHA-256, and Git tree;
5. all declared validation commands in a credential-isolated environment;
6. output credential redaction;
7. independent Reviewer inspection of the complete verified Diff and test data;
8. bounded in-Campaign repairs;
9. evidence gates;
10. a real local Commit;
11. JSON and Markdown reports.

Local mode does not push and does not create a Pull Request.

`--allow` and `--check` remain mandatory security boundaries. QwenPaw does not
automatically grant file write permissions or execute model-generated commands.

## 5. Run a Draft PR Campaign

```bash
qwenpaw campaign run \
  https://github.com/owner/repository/issues/123 \
  --allow src/package/fix.py \
  --allow tests/test_fix.py \
  --check "pytest -q tests/test_fix.py" \
  --delivery draft_pr \
  --report campaign-result.json
```

After the local evidence gates pass, Draft PR mode:

- commits the verified tree;
- pushes only the `autoresearch/issue-*` branch;
- creates one Draft PR;
- supports a same-name personal Fork as the push repository;
- validates the PR Head identity and Commit SHA;
- polls CI and Review status;
- never merges and never enables auto-merge.

Delivery states include:

- `ci_waiting`;
- `review_waiting`;
- `needs_revision`;
- `merge_ready`.

`merge_ready` is evidence only. Merge remains a separate human repository action.

## 6. Revise the same branch and PR

When CI fails or Review requests changes:

```bash
qwenpaw campaign revise CAMPAIGN_ID \
  --rounds 3 \
  --feedback "Address the failing Windows check" \
  --report campaign-result.json
```

The top-level compatibility command is also available:

```bash
qwenpaw campaign-revise CAMPAIGN_ID --rounds 3
```

Every revision:

- requires the preserved worktree to be clean;
- requires its branch and HEAD to match the last delivered Commit;
- reuses the same branch and existing PR URL;
- reads stored and live CI/Review feedback;
- creates a new Run ID;
- regenerates Diff, test, review, Commit, and delivery evidence;
- reruns every validation command;
- invokes the independent Reviewer again;
- pushes the same branch when using Draft PR delivery;
- never creates a second PR;
- stops at the original revision budget.

Revision history is included in the durable Campaign state and final report.

## 7. Reconnect, inspect, and refresh

```bash
qwenpaw campaign-watch CAMPAIGN_ID
qwenpaw campaign status CAMPAIGN_ID
qwenpaw campaign-history --limit 20
qwenpaw campaign-refresh CAMPAIGN_ID
```

The API snapshot is durable. If the service stops during a non-terminal run,
the restored state becomes `needs_revision` rather than silently resuming or
claiming success.

## 8. Inspect recovery state

```bash
qwenpaw campaign recovery CAMPAIGN_ID
```

Compatibility form:

```bash
qwenpaw campaign-recovery CAMPAIGN_ID
```

The response identifies the preserved worktree and whether revision or cleanup
is currently allowed.

## 9. Safely clean a Campaign worktree

```bash
qwenpaw campaign cleanup-worktree CAMPAIGN_ID \
  --confirm CLEANUP_AUTORESEARCH_CAMPAIGN_WORKTREE
```

Before deletion, QwenPaw verifies:

- the branch uses `autoresearch/issue-*`;
- the worktree is registered with the expected source repository;
- the worktree path remains inside that source repository;
- no Campaign task is currently running.

It then exports:

- a binary patch for tracked changes;
- Git status;
- every regular untracked file with its relative path, byte count, and SHA-256;
- a recovery manifest.

Cleanup removes only the local worktree and local AutoResearch branch. It does
not close a PR, delete a remote branch, or alter the source working tree.

## 10. Apply a verified local result

For local-only results, the existing control command can apply the verified
Patch to a clean target checkout without committing it automatically:

```bash
qwenpaw campaign-local apply CAMPAIGN_ID \
  --repository /path/to/target/repository
```

Review and commit the staged result using the repository's normal workflow.

## 11. Complete local release validation

After synchronizing the completed branch, run one command:

```bash
uv run --extra test python scripts/validate_campaign_release.py \
  --output-dir /tmp/qwenpaw-campaign-validation
```

The validator performs no remote write. It runs:

- targeted Campaign tests;
- the complete Research Ledger and Campaign Router/CLI unit suite;
- local Git Campaign integration tests;
- `compileall`;
- `git diff --check`;
- mypy and flake8 on the Campaign feature files.

Untruncated logs and `summary.json` are written to the output directory.
Temporary diagnostic runs can omit broad or quality stages:

```bash
uv run --extra test python scripts/validate_campaign_release.py \
  --skip-broad \
  --skip-quality \
  --output-dir /tmp/qwenpaw-campaign-smoke
```

Those skip flags are not suitable for final release approval.

## 12. Remote Draft PR E2E

The guarded dedicated-test-repository procedure remains documented in
`docs/research/remote-draft-pr-e2e.md`. It requires separate confirmation tokens
and still exposes no merge operation.
