from __future__ import annotations

from qlib_platform.research.workflow.parallel_executor import (
    ParallelizationPolicy,
    benchmark_executor,
    create_research_executor,
)


def _square(value: int) -> int:
    return value * value


def test_parallel_policy_keeps_small_studies_local() -> None:
    policy = ParallelizationPolicy(min_jobs=16, min_estimated_cpu_seconds=600)
    assert not policy.should_parallelize(job_count=8, estimated_seconds_per_job=100)
    assert not policy.should_parallelize(job_count=20, estimated_seconds_per_job=10)
    assert policy.should_parallelize(job_count=20, estimated_seconds_per_job=40)


def test_serial_executor_is_default_and_deterministic() -> None:
    executor = create_research_executor()
    try:
        assert executor.map(_square, [1, 2, 3]) == [1, 4, 9]
        benchmark = benchmark_executor(executor, _square, [1, 2, 3])
    finally:
        executor.close()
    assert benchmark.backend == "serial"
    assert benchmark.jobs == 3
    assert benchmark.jobs_per_second > 0


def test_process_executor_is_opt_in() -> None:
    executor = create_research_executor("process", max_workers=2)
    try:
        assert executor.map(_square, [2, 3]) == [4, 9]
    finally:
        executor.close()
