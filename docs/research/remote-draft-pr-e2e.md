# Guarded AutoResearch Remote Draft PR E2E

This workflow validates the complete AutoResearch delivery path while keeping
all destructive actions behind explicit human gates. It never exposes a merge
command and never enables automatic merge.

## Required preparation

1. Use a dedicated GitHub test repository.
2. Create an open test Issue with the label `autoresearch-e2e`.
3. Ensure the local repository is clean and `origin` points to the exact test
   repository.
4. Prepare an Episode Package whose repository, Issue number, base revision,
   allowed paths, validation commands, and acceptance criteria are explicit.
5. Prepare a candidate patch without committing it to the source repository.
6. Authenticate the GitHub CLI with permission to push a branch and create a
   Draft Pull Request in the dedicated test repository.

## Stage 1: local-only verification

```bash
qwenpaw campaign local-verify \
  --repository /path/to/test-repository \
  --episode /path/to/episode.json \
  --patch /path/to/candidate.patch \
  --report-dir /tmp/autoresearch-local-report
```

This creates an isolated worktree, applies the patch, validates the exact Git
diff and changed paths, runs the Episode commands, creates a local commit,
checks both evidence gates, writes JSON and Markdown reports, and cleans the
worktree. It performs no remote write.

## Stage 2: guarded remote Draft PR E2E

```bash
qwenpaw campaign remote-e2e \
  --repository /path/to/test-repository \
  --episode /path/to/episode.json \
  --patch /path/to/candidate.patch \
  --report-dir /tmp/autoresearch-remote-report \
  --base-branch main \
  --allow-repository owner/test-repository \
  --confirm ENABLE_REMOTE_DRAFT_PR_E2E \
  --monitor-attempts 10 \
  --monitor-interval 30
```

Before any write, the command checks:

- the Episode repository is in the explicit allowlist;
- the local `origin` resolves to the same GitHub repository;
- the Issue is open and carries the dedicated E2E label;
- the exact confirmation token was supplied;
- the branch uses `autoresearch/e2e/`;
- the remote branch does not already exist;
- local verification completed with a host-observed Git commit;
- the created Pull Request URL belongs to the intended repository;
- the monitored GitHub HEAD equals the host-verified commit.

The command pushes one protected E2E branch and creates one Draft Pull Request.
It does not mark the PR ready and does not merge it.

### Remote statuses

- `ci_waiting`: one or more checks are pending or no checks are visible yet.
- `review_waiting`: CI passed but human review is still required.
- `needs_revision`: CI failed, was cancelled, or review requested changes.
- `draft_validated`: automated evidence is green, but the PR remains Draft and
  requires a second human action.

## Stage 3: human promotion to Ready

```bash
qwenpaw campaign promote-ready \
  --report /tmp/autoresearch-remote-report/remote-verification.json \
  --allow-repository owner/test-repository \
  --confirm PROMOTE_AUTORESEARCH_DRAFT_PR
```

Promotion requires the remote report, an allowlisted repository, matching live
PR identity, matching verified commit, the E2E marker, an open protected branch,
and successful CI checks. It runs `gh pr ready`; it does not merge.

The resulting state is `ready_for_human_merge`. Any merge remains a separate,
manual repository action outside AutoResearch.

## Stage 4: close and clean the E2E PR

```bash
qwenpaw campaign cleanup-e2e \
  --report /tmp/autoresearch-remote-report/remote-verification.json \
  --allow-repository owner/test-repository \
  --confirm CLOSE_AUTORESEARCH_E2E_PR
```

Cleanup revalidates the repository, PR URL, branch prefix, commit identity, and
E2E body marker before closing the known test PR and deleting only its test
branch.

## Prohibited behavior

The E2E modules intentionally contain no operation for:

- merging a Pull Request;
- enabling auto-merge;
- force-pushing an existing branch;
- writing to a repository outside the explicit allowlist;
- creating a non-Draft Pull Request;
- using `main`, `master`, or the configured base branch as the E2E branch;
- trusting an Agent response as evidence of a code change.
