# CLAUDE.md — methodology-runtime

The sandbox runtime for Lake workspace methodologies (lake#655). Org-wide rules live in
`oramasearch/orama-bridge` `CLAUDE.md` and apply in full: no AI attribution in commits or PRs,
name the actual mechanism (the org's banned-word rule), no time or size estimates, commit means push, merge commits only, no CI
workflows, this repository and its Docker Hub repository stay private.

- **The harness is the standard library only.** The image must carry no third-party package of
  its own; whatever a methodology needs comes from its own hash-locked lock file, installed into
  `/methodology/.packages` when its image is built.
- **Sandbox checks run before the person's code is imported**, and a refusal never prints a
  credential's value, only its name or path.
- **Only `stage` and `collect` talk to Lake**, and only to `methodology_run_input` and
  `methodology_run_output`. The run command never opens a connection.
- The contract with Lake and the operator is `docs/cli.md` and `docs/sandbox.md`; change them in the
  same commit as the code, with tests.
- Test: `python3 -m unittest` (also on Python 3.12, the image's version).
