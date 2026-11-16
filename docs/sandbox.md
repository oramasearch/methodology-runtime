# The sandbox

A workspace methodology is code Orama did not write, so it runs as untrusted code. The Orama
operator (orama-operator#291) runs each
run as three pods sharing one volume (`/work`), so the untrusted code never shares a network
namespace with a credential:

| Pod | Image | Credential | Network | Command |
|---|---|---|---|---|
| stage | this image, unchanged | the run's `lmr.<run id>.<secret>` at `/var/run/orama/credential` | Lake's Service only | `methodology stage` writes `/work/in` |
| run | the methodology's image (FROM this one) | none | none, unless the person granted it | `methodology run` reads `/work/in`, writes `/work/out` |
| collect | this image, unchanged | the same run credential | Lake's Service only | `methodology collect` posts `/work/out` |

A **model deployment** (lake#690) is the same split: a `stage-model` Job (this image, with the
deployment's credential `lmd.<deployment id>.<secret>`, Lake's Service only) writes the model to a
volume, and a long-running **serve** pod (the methodology's image, `methodology serve`) mounts it
read-only with no credential and no network unless granted. The serve pod accepts connections
from Lake's pods only, on its port; it opens none of its own. It makes the same checks below
before importing the code.

The stage and collect pods run the pristine runtime image, never the methodology's own image, so
the code that handles the credential is never code the methodology could replace.

## Nothing the run pod writes runs in a credential pod

`/work` is shared, so stage, collect and stage-model treat it as written by the untrusted code:

- **No module is imported from it** (methodology-runtime#4). The image's working directory is
  `/work`, and `python3 -m` would put the working directory first on `sys.path`, so a planted
  `/work/orama_methodology/` or `/work/json.py` would run next to the credential. The command starts
  Python with `-P` (the image also sets `PYTHONSAFEPATH=1`), and the package drops the working
  directory and anything under `/work` from `sys.path` before importing anything else. The operator
  also starts these pods in their own `/tmp` with `PYTHONSAFEPATH=1`
  ([orama-operator#821](https://github.com/oramasearch/orama-operator/pull/821)); the runtime does not
  depend on it.
- **Its files are read as data, and only regular files** (methodology-runtime#5). Collect opens
  `/work/out` and each output file without following symbolic links and checks the opened file is a
  regular file, so a link to `/var/run/orama/credential` is refused with the finding
  `run.output_file` instead of being uploaded as the log or the model. A hard link cannot reach the
  credential: it is on a different filesystem from the work volume.

## What the run pod may see

- `/work/in/spec.json` and `/work/in/data.jsonl` (read-only), `/work/out` (writable), `/tmp`;
- `/methodology` (its code) and `/methodology/.packages` (its locked packages);
- the Kubernetes API address variables that kubelet always injects (`KUBERNETES_SERVICE_HOST`,
  `KUBERNETES_PORT*`): an address, not a credential, and unreachable without network.

## What it must not see

- any credential: no Secret, no service account token (`automountServiceAccountToken: false`), no
  Lake or Zetta address or key, no cloud identity token;
- any network, unless the run's `network` is true: then the internet only, never cluster, node,
  private or metadata addresses (the operator's NetworkPolicy).

## The checks the harness makes first

Before importing any of the methodology's code, `methodology run` checks the sandbox and fails the
run (exit 3, `result.json` with the finding) when it is wrong:

- `sandbox.credentials` fails if an environment variable's name matches `LAKE_*`, `ZETTA_*`,
  `*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*CREDENTIAL*`, `AWS_*`, `AZURE_*`, `GOOGLE_*` or
  `KUBERNETES_*` (except the API address variables above), or if
  `/var/run/secrets/kubernetes.io/serviceaccount{,/token}`, `/var/run/orama/credential`, or a cloud
  workload identity token file exists. The finding names the variables and files, never a value.
- `sandbox.network` (only when the spec's `network` is false) fails if `pypi.org` resolves or a TCP
  connection to `1.1.1.1:443`, `8.8.8.8:53` or `169.254.169.254:80` opens within two seconds.

These checks are a second line behind the pod the operator builds; they make a wrongly built pod
fail loudly instead of running code next to a credential. Everything in `/work/out` is written by
code that is not trusted, so Lake checks the shape of what collect sends (metrics are numbers, sizes
are bounded) and records it as the methodology's own report.

## Proving it

- `python3 -m unittest tests.test_harness.SandboxRefusalTests tests.test_sandbox` — a credential
  variable, a token file and an ungranted network each refuse the run before the code is imported
  (a canary file the code would write stays absent); a granted network is not probed.
- `python3 -m unittest tests.test_safe_path tests.test_transfer.SymbolicLinkTests` — modules planted in
  the working directory (`orama_methodology/`, `json.py`, `dataclasses.py`, …) are not imported by
  the command, stage or collect, and collect reads no symbolic link, named pipe or linked output
  directory.
- `tools/sandbox-smoke.sh` — with a container runtime: the example runs with `--network none`, no
  environment, a read-only root, no capabilities and uid 10001; the same image refuses
  `-e LAKE_TOKEN=…` and a run with network it was not granted (exit 3).
