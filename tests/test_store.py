import hashlib

import pytest

from experiment_tracker import ExperimentError, ExperimentStore


def test_run_lifecycle_and_summary(tmp_path):
    database = tmp_path / "experiments.db"
    with ExperimentStore(database) as store:
        run = store.create_run(
            "churn",
            "baseline",
            notes="first model",
            started_at="2026-08-28T10:00:00+00:00",
        )
        store.log_params(run.id, {"seed": 42, "model": "logistic"})
        store.log_metric(run.id, "f1", 0.72, step=0, logged_at="2026-08-28T10:01:00+00:00")
        store.log_metric(run.id, "f1", 0.75, step=1, logged_at="2026-08-28T10:02:00+00:00")
        finished = store.finish_run(
            run.id,
            ended_at="2026-08-28T10:03:00+00:00",
        )
        summary = store.summary(run.id)

    assert finished.status == "completed"
    assert summary["params"] == {"model": "logistic", "seed": 42}
    assert [metric["value"] for metric in summary["metrics"]] == [0.72, 0.75]


def test_best_run_uses_latest_step_and_respects_direction(tmp_path):
    with ExperimentStore(tmp_path / "runs.db") as store:
        first = store.create_run("forecast", "first")
        store.log_metric(first.id, "rmse", 0.5, step=0)
        store.log_metric(first.id, "rmse", 0.4, step=1)
        store.finish_run(first.id)
        second = store.create_run("forecast", "second")
        store.log_metric(second.id, "rmse", 0.3, step=0)
        store.finish_run(second.id)

        result = store.best_run("forecast", "rmse", minimize=True)

    assert result is not None
    run, value = result
    assert run.id == second.id
    assert value == 0.3


def test_artifact_hash_is_recorded(tmp_path):
    artifact_path = tmp_path / "model.bin"
    artifact_path.write_bytes(b"model-output")
    expected = hashlib.sha256(b"model-output").hexdigest()

    with ExperimentStore(tmp_path / "runs.db") as store:
        run = store.create_run("demo", "artifact")
        artifact = store.add_artifact(run.id, artifact_path)

    assert artifact.sha256 == expected
    assert artifact.size_bytes == len(b"model-output")


def test_invalid_operations_are_rejected(tmp_path):
    with ExperimentStore(tmp_path / "runs.db") as store:
        run = store.create_run("demo", "safe")
        with pytest.raises(ExperimentError, match="finite"):
            store.log_metric(run.id, "loss", float("nan"))
        with pytest.raises(ExperimentError, match="finite"):
            store.log_metric(run.id, "loss", float("inf"))
        with pytest.raises(ExperimentError, match="does not exist"):
            store.log_metric(999, "loss", 0.1)
        with pytest.raises(ExperimentError, match="Final status"):
            store.finish_run(run.id, status="running")


def test_run_filtering_and_upsert(tmp_path):
    with ExperimentStore(tmp_path / "runs.db") as store:
        run = store.create_run("demo", "one")
        store.log_metric(run.id, "accuracy", 0.5)
        store.log_metric(run.id, "accuracy", 0.8)
        store.log_param(run.id, "seed", 1)
        store.log_param(run.id, "seed", 2)
        store.create_run("other", "two")

        runs = store.list_runs(project="demo")
        summary = store.summary(run.id)

    assert len(runs) == 1
    assert summary["metrics"][0]["value"] == 0.8
    assert summary["params"]["seed"] == 2
