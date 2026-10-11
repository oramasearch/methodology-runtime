# The methodology interface (interface 1)

A workspace methodology is a directory in one of the workspace's repositories (Lake records the
repository, revision and path). It holds three files.

## `methodology.json`

```json
{
  "interface": 1,
  "name": "Logistic regression (standard library)",
  "description": "What it is for, in a sentence or two.",
  "entrypoint": "methodology.py",
  "inputs": {
    "kinds": ["tabular"],
    "target": {"required": true, "kind": "binary"},
    "required_features": []
  },
  "parameters": [
    {"name": "learning_rate", "kind": "float", "default": 0.5, "minimum": 0.001, "maximum": 5, "log": true},
    {"name": "epochs", "kind": "int", "default": 200, "minimum": 1, "maximum": 5000},
    {"name": "solver", "kind": "categorical", "default": "batch", "choices": ["batch", "online"]},
    {"name": "balanced", "kind": "bool", "default": false}
  ],
  "metrics": ["auc", "accuracy", "log_loss"],
  "explain": true,
  "packages": "requirements.lock",
  "resources": {"cpu": "1", "memory": "2Gi", "gpu": 0, "timeout_seconds": 3600}
}
```

| Field | Rule |
|---|---|
| `interface` | `1` |
| `name` | required, not blank |
| `entrypoint` | a relative `.py` path inside the methodology; default `methodology.py` |
| `inputs.kinds` | one or more of `tabular`, `text`, `time_series`, `graph`, `image`, `audio`, `document` |
| `inputs.target` | `{required, kind}`; `kind` is `binary`, `multiclass`, `regression` or `none`; `required` is false exactly when `kind` is `none` |
| `inputs.required_features` | column names the data must have |
| `parameters` | each has a `name` (`^[a-z_][a-z0-9_]*$`, unique), a `kind` (`int`, `float`, `categorical`, `bool`) and a `default`; `int`/`float` may have `minimum`, `maximum` and `log` (a log scale needs a minimum above zero); `categorical` needs `choices` containing the default. Experiments search over these |
| `metrics` | base names (`auc`, `rmse`), at least one. The runner reports each twice, as `in_sample_<name>` (the rows it trained on) and `held_out_<name>` (rows it never saw). Never list the prefixed names |
| `explain` | true when `explain` is implemented |
| `packages` | the lock file's path; default `requirements.lock` |
| `resources` | `cpu` (`"1"`, `"500m"`), `memory` (`"2Gi"`), `gpu` (whole number), `timeout_seconds` (1 to 86400). Lake may cap them |

Unknown keys are refused, so a misspelt field is reported rather than ignored.

## `methodology.py`

```python
def fit(data, params):        # -> artifact: bytes, or any picklable object
def predict(artifact, data):  # -> list, one prediction per row
def score(artifact, data):    # -> dict, every manifest metric (base name) to a finite number
def explain(artifact, data):  # optional -> list of dict, feature to number, one per row
```

`params` is the manifest's defaults overlaid with the run's parameters, each checked against its
declaration first. The harness splits the data with the run's `holdout_fraction` and `seed`, calls
`fit` on the training rows, `predict` on the held-out rows, `score` on both, and `explain` on up to
20 held-out rows. The artifact is kept as bytes when `fit` returns bytes, and pickled otherwise.

## `Data`

| Member | What it is |
|---|---|
| `data.rows` | a list of dicts, one per row |
| `data.columns` | every column seen, first-seen order |
| `data.target` | the target column's name, or `None` |
| `data.features` | the run's features, or every column but the target |
| `data.X()` | feature values, one list per row, in `features` order (`None` where missing) |
| `data.y()` | target values, one per row, or `None` |
| `len(data)`, `data.subset(indices)` | the row count; a `Data` of the rows at those indices |

## The lock file

A pip requirements file in which every requirement is pinned with `==` and carries at least one
`--hash=sha256:<64 hex>`, for example:

```text
numpy==2.1.3 \
    --hash=sha256:<hash of the linux x86_64 cp312 wheel>
```

Environment markers are allowed; options such as `--index-url`, `-e`, `-r` and URLs are not. An empty
file (or none, when the manifest does not name one) means the standard library only. The image
build installs it with `pip install --require-hashes --no-deps --only-binary=:all:` into
`/methodology/.packages`, so every dependency must be listed, and only wheels are used: no package's
build script runs. The run itself has no network to install anything.

## What Validate reports

`methodology run` in `validate` mode (and `methodology validate-manifest` locally, without data)
reports every finding at once: each manifest field, each lock line, each run parameter, the data
(target column present, required features present, at least two rows), each interface function,
then, once all of those pass, `fit`, `predict` (one prediction per row), `score` (every metric a
finite number, in sample and held out), `explain` when declared, and whether the artifact can be
kept. A validate run keeps no artifact. See `examples/logistic` (passes) and `examples/broken`
(missing `score`, unpinned lock line).
