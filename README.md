# Experiment Tracker Lite

[![CI](https://github.com/hieutran-tud/experiment-tracker-lite/actions/workflows/ci.yml/badge.svg)](https://github.com/hieutran-tud/experiment-tracker-lite/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE) [![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)

Experiment Tracker Lite is a local SQLite registry for machine-learning runs. It records the metadata that is often scattered across notebooks: run names, parameters, metrics by step, completion status, notes, and SHA-256 fingerprints for artifacts.

It is intentionally small enough for a personal project while preserving useful engineering properties: a durable relational schema, foreign keys, transactional writes, deterministic best-run selection, and an API that is easy to test.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

python -m experiment_tracker experiments.db create \
  --project churn --name baseline --notes "Logistic regression baseline"
python -m experiment_tracker experiments.db log-param \
  --run-id 1 --key learning_rate --value 0.01
python -m experiment_tracker experiments.db log-metric \
  --run-id 1 --name f1 --value 0.81
python -m experiment_tracker experiments.db finish --run-id 1
python -m experiment_tracker experiments.db best --project churn --metric f1
```

The `experiment-tracker` console script is equivalent when the Python scripts directory is on `PATH`.

## Python API

```python
from experiment_tracker import ExperimentStore

with ExperimentStore("experiments.db") as store:
    run = store.create_run("churn", "random-forest")
    store.log_params(run.id, {"n_estimators": 200, "seed": 42})
    store.log_metric(run.id, "f1", 0.84)
    store.finish_run(run.id)
```

## Example output

A short run lifecycle looks like this:

```text
$ experiment-tracker experiments.db create --project churn --name baseline
Created run #1: churn/baseline
$ experiment-tracker experiments.db log-metric --run-id 1 --name f1 --value 0.81
Logged f1=0.81 at step 0 for run #1
$ experiment-tracker experiments.db best --project churn --metric f1
#1 churn/baseline: f1=0.81
```


## What makes it useful

- Metrics are keyed by `(run, metric, step)`, so rerunning a step updates it rather than creating ambiguity.
- `best_run` only considers completed runs and uses the latest recorded step.
- Artifacts are not copied into the database; their path, size, and SHA-256 digest are recorded for provenance.
- The database can be inspected with any SQLite client and backed up as one file.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

## Limitations and roadmap

This project is designed for local or single-machine workflows. A future version could add a web view, tags, dataset lineage, and a storage adapter for object stores without changing the run model.
