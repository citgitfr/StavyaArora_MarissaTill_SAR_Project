# AI Use Log

Claude Code wrote list_ncei.py and pull_hfr_ncei.py, pull_fixtures.py and look.py, and I reviewed and ran all of them.
Claude Code (Opus 5.5) wrote forcing_tools.py and its tests from docs/dev_notes.md; we read the code, wrote the thresholds ourselves, and ran the tests and the tools on the real data.
2026-11-15: Claude Code (Opus 5.5) drafted the memory design (memory/MEMORY.md, memory/USER.md) and the matching changes to .claude/settings.json, PERMISSIONS.md and CLAUDE.md, from the TA's feedback; we reviewed and approved each edit.
2026-11-15: Claude Code (Opus 5.5) wrote src/baseline.py and tests/test_baseline.py from the SKILL.md repair menu and the frozen thresholds, and ran them; we chose the rule-table design and review the code before freezing it. It also reviewed the agent smoke-test and heldout-probe transcripts, and wrote scripts/make_eval_workspace.py to isolate evaluation runs from the fault plan, fixtures and results.
