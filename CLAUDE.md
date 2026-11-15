# CLAUDE.md — methodology-runtime

The sandbox runtime for Lake workspace methodologies.

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

## Rules

- **Branches:** branch from `dev`, and open every pull request against `dev`. `main` holds the last
  release. Pull requests are merged with merge commits, never squashed.
- **Documentation in the same change:** `README.md` and the pages under `docs/` change in the same
  commit as the code they describe.
- **No AI attribution** in commits, pull requests, tags or release notes: no `Co-Authored-By`
  trailers for an assistant, no "Generated with" lines.
- **Never write the word "seam"**; name the mechanism (interface, extension point, injected
  dependency, boundary).
- **No time or size estimates**; report what was done.
- Report vulnerabilities privately, as `SECURITY.md` describes, never in a public issue.
