# Bounded Automatic Campaign Revision

Automatic revision is disabled by default. Enable it explicitly on the normal
`campaign run` command:

```bash
qwenpaw campaign run \
  https://github.com/owner/repository/issues/123 \
  --allow src/package/fix.py \
  --allow tests/test_fix.py \
  --check "pytest -q tests/test_fix.py" \
  --delivery draft_pr \
  --auto-revise-rounds 2 \
  --report campaign-result.json
```

The automatic loop starts only when the initial Campaign has produced a durable
report with status `needs_revision`. It does not run for blocked, failed,
cancelled, or successful results.

Each automatic round calls the same revision API used by:

```bash
qwenpaw campaign revise CAMPAIGN_ID
```

Therefore it preserves the same safety contract:

- same Campaign identity;
- same isolated worktree;
- same local and remote branch;
- same existing Draft PR URL and number;
- original explicit file allowlist;
- original validation commands;
- new host-derived Diff and Git tree;
- full validation rerun;
- new independent Reviewer decision;
- new Commit on the same branch;
- no second PR;
- no automatic merge.

The server also enforces the revision budget stored in the original Campaign
request. The CLI budget cannot bypass that server-side limit.
