# Contributing to methodology-runtime

methodology-runtime is the sandbox runtime for a Lake workspace's own methodologies. The contract
with Lake and the Orama operator is [`docs/cli.md`](docs/cli.md) and
[`docs/sandbox.md`](docs/sandbox.md); the interface a methodology implements is
[`docs/interface.md`](docs/interface.md).

## Test

```sh
python3 -m unittest
```

Run it on Python 3.12 too, the image's version. Say in your pull request which tests you ran.

## Rules for changes

- **The harness is the standard library only.** The image carries no third-party package of its
  own; whatever a methodology needs comes from its own hash-locked lock file, installed into
  `/methodology/.packages` when its image is built.
- **Sandbox checks run before the person's code is imported**, and a refusal never prints a
  credential's value, only its name or path.
- **Only `stage` and `collect` talk to Lake**, and only to `methodology_run_input` and
  `methodology_run_output`. The run command never opens a connection.
- Change `docs/cli.md` and `docs/sandbox.md` in the same pull request as the code, with tests.
- Branch from `dev` and open the pull request against `dev`; `main` holds the last release.

## Security

Report a vulnerability privately, as [`SECURITY.md`](SECURITY.md) describes, never in a public issue.
