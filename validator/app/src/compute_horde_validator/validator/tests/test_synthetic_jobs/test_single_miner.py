import asyncio
import uuid
from collections.abc import Callable
from unittest.mock import patch

import bittensor
import pytest
from asgiref.sync import sync_to_async
from pytest_mock import MockerFixture

from compute_horde_validator.validator.models import (
    Miner,
    SyntheticJob,
    SystemEvent,
)
from compute_horde_validator.validator.synthetic_jobs.batch_run import execute_synthetic_batch_run
from compute_horde_validator.validator.tests.transport import MinerSimulationTransport

from ..helpers import check_system_events
from .mock_generator import (
    MOCK_SCORE,
    NOT_SCORED,
    MockSyntheticJobGeneratorFactory,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.django_db(databases=["default", "default_alias"], transaction=True),
]


@pytest.fixture(autouse=True)
def _patch_generator_factory(mocker: MockerFixture, job_uuid: uuid.UUID):
    mocker.patch(
        "compute_horde_validator.validator.synthetic_jobs.generator.current.synthetic_job_generator_factory",
        MockSyntheticJobGeneratorFactory(uuids=[job_uuid]),
    )


async def test_execute_miner_synthetic_jobs_success(
    miner: Miner,
    axon_dict: dict[str, bittensor.AxonInfo],
    manifest_message: str,
    executor_ready_message: str,
    accept_job_message: str,
    job_finish_message: str,
    create_simulation_miner_client: Callable,
    transport: MinerSimulationTransport,
    job_uuid: uuid.UUID,
):
    await transport.add_message(manifest_message, send_before=1)
    await transport.add_message(accept_job_message, send_before=1)
    await transport.add_message(executor_ready_message, send_before=0)
    await transport.add_message(job_finish_message, send_before=2)

    await asyncio.wait_for(
        execute_synthetic_batch_run(
            axon_dict,
            [miner],
            create_miner_client=create_simulation_miner_client,
        ),
        timeout=1,
    )

    await check_synthetic_job(job_uuid, miner, SyntheticJob.Status.COMPLETED, MOCK_SCORE)
    await sync_to_async(check_system_events)(
        SystemEvent.EventType.MINER_SYNTHETIC_JOB_SUCCESS, SystemEvent.EventSubType.SUCCESS
    )


@patch(
    "compute_horde_validator.validator.synthetic_jobs.batch_run._JOB_RESPONSE_EXTRA_TIMEOUT", 0.05
)
async def test_execute_miner_synthetic_jobs_success_timeout(
    miner: Miner,
    axon_dict: dict[str, bittensor.AxonInfo],
    manifest_message: str,
    executor_ready_message: str,
    accept_job_message: str,
    job_finish_message: str,
    create_simulation_miner_client: Callable,
    transport: MinerSimulationTransport,
    job_uuid: uuid.UUID,
):
    await transport.add_message(manifest_message, send_before=1)
    await transport.add_message(accept_job_message, send_before=1)
    await transport.add_message(executor_ready_message, send_before=0)
    await transport.add_message(job_finish_message, send_before=2, sleep_before=2)

    await asyncio.wait_for(
        execute_synthetic_batch_run(
            axon_dict,
            [miner],
            create_miner_client=create_simulation_miner_client,
        ),
        timeout=3,
    )

    await check_synthetic_job(job_uuid, miner, SyntheticJob.Status.FAILED, NOT_SCORED)
    await sync_to_async(check_system_events)(
        SystemEvent.EventType.MINER_SYNTHETIC_JOB_FAILURE,
        SystemEvent.EventSubType.JOB_EXECUTION_TIMEOUT,
    )


async def test_execute_miner_synthetic_jobs_job_failed(
    miner: Miner,
    axon_dict: dict[str, bittensor.AxonInfo],
    manifest_message: str,
    executor_ready_message: str,
    accept_job_message: str,
    job_failed_message: str,
    create_simulation_miner_client: Callable,
    transport: MinerSimulationTransport,
    job_uuid: uuid.UUID,
):
    await transport.add_message(manifest_message, send_before=1)
    await transport.add_message(accept_job_message, send_before=1)
    await transport.add_message(executor_ready_message, send_before=0)
    await transport.add_message(job_failed_message, send_before=2)

    await asyncio.wait_for(
        execute_synthetic_batch_run(
            axon_dict,
            [miner],
            create_miner_client=create_simulation_miner_client,
        ),
        timeout=1,
    )

    await check_synthetic_job(job_uuid, miner, SyntheticJob.Status.FAILED, NOT_SCORED)
    await sync_to_async(check_system_events)(
        SystemEvent.EventType.MINER_SYNTHETIC_JOB_FAILURE, SystemEvent.EventSubType.FAILURE
    )


async def test_execute_miner_synthetic_jobs_job_declined(
    miner: Miner,
    axon_dict: dict[str, bittensor.AxonInfo],
    manifest_message: str,
    decline_job_message: str,
    create_simulation_miner_client: Callable,
    transport: MinerSimulationTransport,
    job_uuid: uuid.UUID,
):
    await transport.add_message(manifest_message, send_before=1)
    await transport.add_message(decline_job_message, send_before=1)

    await asyncio.wait_for(
        execute_synthetic_batch_run(
            axon_dict,
            [miner],
            create_miner_client=create_simulation_miner_client,
        ),
        timeout=1,
    )

    await check_synthetic_job(job_uuid, miner, SyntheticJob.Status.FAILED, NOT_SCORED)
    await sync_to_async(check_system_events)(
        SystemEvent.EventType.MINER_SYNTHETIC_JOB_FAILURE, SystemEvent.EventSubType.JOB_NOT_STARTED
    )


@patch("compute_horde_validator.validator.synthetic_jobs.batch_run._GET_MANIFEST_TIMEOUT", 0.2)
async def test_execute_miner_synthetic_jobs_no_manifest(
    miner: Miner,
    axon_dict: dict[str, bittensor.AxonInfo],
    create_simulation_miner_client: Callable,
):
    await asyncio.wait_for(
        execute_synthetic_batch_run(
            axon_dict,
            [miner],
            create_miner_client=create_simulation_miner_client,
        ),
        timeout=1,
    )

    assert not await SyntheticJob.objects.aexists()
    await sync_to_async(check_system_events)(
        SystemEvent.EventType.MINER_SYNTHETIC_JOB_FAILURE, SystemEvent.EventSubType.MANIFEST_TIMEOUT
    )


async def check_synthetic_job(job_uuid: uuid.UUID, miner: Miner, status: str, score: float):
    job = await SyntheticJob.objects.aget()
    assert job.job_uuid == job_uuid
    assert job.miner_id == miner.pk
    assert job.status == status
    assert job.score == score
