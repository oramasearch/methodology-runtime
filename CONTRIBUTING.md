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
Thank you for helping. This document says how to build and test methodology-runtime, how a change gets in,
and the one thing every contributor does first: sign the Contributor License
Agreement.

## Before you start

- For a bug, open an issue with what you did, what you expected and what happened, and the
  version or commit.
- For a larger change (a new feature, a new dependency, a change to an interface), open an issue
  first so the approach can be agreed before you write it.
- **Security issues are never reported in a public issue.** See [SECURITY.md](SECURITY.md): write
  to **security@orama.com**.

## Build and test

You need Python 3.12 or later. The runtime uses only the standard library.

```sh
python3 -m unittest                              # the whole suite
./bin/methodology validate-manifest examples/logistic
docker build -t methodology-runtime:local .      # a local image
tools/sandbox-smoke.sh                           # run the image as a run pod does, with no registry
```

## Making a change

1. Fork the repository and branch from `dev`. `main` is the last release; it moves only when a
   release is cut.
2. Keep one change per pull request, with the tests that prove it. A change in behavior updates
   the documentation it touches in the same pull request.
3. Write commit messages in the [Conventional Commits](https://www.conventionalcommits.org/) form,
   for example `fix(api): reject an empty name`.
4. Sign the Contributor License Agreement before your first pull request is merged (next section).
5. Open the pull request against `dev`. Start its description with what changes, why, and how.

A maintainer reviews it. Pull requests are merged with a merge commit, so every commit you make
stays in the history.

## Contributor License Agreement

methodology-runtime is owned by OramaSearch, Inc. Every contributor signs the
[Contributor License Agreement](CLA.md) (CLA) once, before their first pull request is merged. Under
it you assign the copyright in your contributions to OramaSearch, Inc., you keep a licence to use
your own contributions for any purpose, and you grant a patent licence for them. The project is
published under the MIT License (see [LICENSE](LICENSE)).

When you open your first pull request, a bot comments on it with a link to the agreement. Sign
there with your GitHub account; it covers all your later contributions to projects of OramaSearch,
Inc. If you contribute as part of your job, your employer signs too: write to
**opensource@orama.com** for the entity agreement.

## License headers

Every source file starts with the line `SPDX-License-Identifier: MIT`, in that file's comment
syntax, for example `// SPDX-License-Identifier: MIT`. Add it to a new file yourself, or run:

```sh
python3 tools/spdx-headers.py           # adds the line where it is missing
python3 tools/spdx-headers.py --check   # lists files without it
```

Code copied from another project keeps its own license header and is listed in
[THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES). Say so in the pull request, with where it came from
and its license; it must be compatible with the MIT License.

## Code of conduct

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md). Report unacceptable
behavior to **conduct@orama.com**.
