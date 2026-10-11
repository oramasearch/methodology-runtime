# methodology-runtime

The sandbox runtime for a Lake workspace's own methodologies
([lake#655](https://github.com/oramasearch/lake/issues/655)). A workspace methodology is Python
code in one of the workspace's repositories; this repository holds:

- `orama_methodology/` — the interface a methodology implements (`fit`, `predict`, `score`,
  optional `explain`), the `Data` object it receives, the manifest and lock-file checks, the
  sandbox checks, and the harness;
- `bin/methodology` — the command the three run pods use: `stage`, `run`, `collect` (plus
  `validate-manifest` for local use and `install-locked` for image builds);
- `Dockerfile` — the private image `oramasearch/methodology-runtime` (Python 3.12 pinned by
  digest, non-root uid 10001, standard library only);
- `templates/methodology.Dockerfile` — the image Lake builds for each methodology, FROM this one;
- `examples/` — a working standard-library logistic regression and a broken example.

## Read

- [docs/interface.md](docs/interface.md) — what a methodology is: the manifest, the functions,
  the data, the lock file.
- [docs/sandbox.md](docs/sandbox.md) — what the run container can and cannot see, and how data
  moves in and out only through Lake.
- [docs/cli.md](docs/cli.md) — the commands, the HTTP calls stage and collect make, exit codes and
  image paths.

## Build and test

```sh
python3 -m unittest                                # the whole suite, standard library only
./bin/methodology validate-manifest examples/logistic
docker build -t methodology-runtime:local .        # a local image; never pushed from here
tools/sandbox-smoke.sh                             # run it as the run pod does, no registry
```

Who runs it: the Orama operator
([orama-operator#291](https://github.com/oramasearch/orama-operator/issues/291)) runs the stage, run
and collect pods; Lake serves `methodology_run_input` and `methodology_run_output` and records the
result in the workspace Zetta.

Private repository; the Docker Hub repository `oramasearch/methodology-runtime` is private.
