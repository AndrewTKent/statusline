# Token scanning and the usage ledger

Two independent tools, both built on the same session JSONLs.

## Token scanning

**Token scanning** (`bin/scan_tokens_core.py` + the `bin/scan-tokens*.py`/`.sh` CLIs) attributes every request to work/personal and to a payer, incrementally, and feeds the `tokens`/`usage`/goal/`bounty` rows plus the work-unit cap columns on the account board. `bin/derive-cap.py` fits those per-account caps from utilization history — it's a manual, unscheduled tool you re-run occasionally, not something cron or launchd calls. Full design, cache schema, and failure modes: [`bin/ARCHITECTURE.md`](../bin/ARCHITECTURE.md).

```bash
python3 bin/scan-tokens.py                 # one-shot full scan
macos/launchd/install-daemon.sh            # long-running watcher; --remove to drop it
```

The work/personal classifier reads `WORK_PATHS`, `PERSONAL_PATHS`, `WORK_KEYWORDS` and `PERSONAL_KEYWORDS` from `statusline.conf` ([configuration.md](configuration.md#token-classifier)); the payer comes from `EMAIL_PAYER_MAP`.

## Overrides and redaction

Per-session entries in `token-scan-overrides.json` take precedence over the classifier. An entry is a tag, or a tag plus time ranges; a range can carry its own tag, a note, and a `redact` flag. The summary counts sessions and ranges marked `redact` in its `redactions` block.

## Durable ledger and archival

**Durable ledger & archival** (`bin/usage-ledger.py`, `bin/archive-transcripts.sh`, `bin/vault-snapshot.sh`) keep a permanent per-day/per-model token ledger at `~/.claude/usage-ledger.json` and mirror Claude Code session JSONLs nightly — rows never pruned, survives transcript cleanup.
