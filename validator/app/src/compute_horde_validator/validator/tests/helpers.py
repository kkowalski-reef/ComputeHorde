import glob
import logging
import numbers
import os
import shlex
import subprocess
from datetime import timedelta
from pathlib import Path
from time import monotonic
from typing import NamedTuple
from unittest import mock

import constance
import numpy as np
from bittensor import Balance
from compute_horde.executor_class import DEFAULT_EXECUTOR_CLASS
from compute_horde.mv_protocol.miner_requests import (
    V0ExecutorReadyRequest,
    V0JobFinishedRequest,
)
from compute_horde.mv_protocol.validator_requests import BaseValidatorRequest
from django.conf import settings
from substrateinterface.exceptions import SubstrateRequestException

from compute_horde_validator.validator.models import SystemEvent
from compute_horde_validator.validator.organic_jobs.facilitator_api import (
    V0FacilitatorJobRequest,
    V1FacilitatorJobRequest,
)
from compute_horde_validator.validator.organic_jobs.miner_client import MinerClient
from compute_horde_validator.validator.synthetic_jobs import batch_run

NUM_NEURONS = 5


logger = logging.getLogger(__name__)


def throw_error(*args):
    raise Exception("Error thrown for testing")


def get_keypair():
    return settings.BITTENSOR_WALLET().get_hotkey()


def get_miner_client(MINER_CLIENT, job_uuid: str):
    return MINER_CLIENT(
        miner_hotkey="miner_hotkey",
        miner_address="ignore",
        miner_port=9999,
        job_uuid=job_uuid,
        my_keypair=get_keypair(),
    )


class MockedAxonInfo(NamedTuple):
    is_serving: bool
    ip: str
    ip_type: int
    port: int


async def mock_get_miner_axon_info(hotkey: str):
    return MockedAxonInfo(is_serving=True, ip_type=4, ip="0000", port=8000)


class MockSyntheticMinerClient(batch_run.MinerClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sent_models = []

    async def connect(self) -> None:
        pass

    async def send(self, data: str | bytes, error_event_callback=None):
        msg = BaseValidatorRequest.parse(data)
        self._sent_models.append(msg)

    def _query_sent_models(self, condition=None, model_class=None):
        result = []
        for model in self._sent_models:
            if model_class is not None and not isinstance(model, model_class):
                continue
            if not condition(model):
                continue
            result.append(model)
        return result


class MockMinerClient(MinerClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sent_models = []

    def miner_url(self) -> str:
        return "ws://miner"

    async def connect(self):
        return

    async def handle_message(self, msg):
        pass

    async def send_model(self, model, error_event_callback=None):
        self._sent_models.append(model)

    def _query_sent_models(self, condition=None, model_class=None):
        result = []
        for model in self._sent_models:
            if model_class is not None and not isinstance(model, model_class):
                continue
            if not condition(model):
                continue
            result.append(model)
        return result


class MockJobStateMinerClient(MockMinerClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.miner_ready_or_declining_future.set_result(
            V0ExecutorReadyRequest(job_uuid=self.job_uuid)
        )
        self.miner_finished_or_failed_future.set_result(
            V0JobFinishedRequest(
                job_uuid=self.job_uuid,
                docker_process_stdout="",
                docker_process_stderr="",
            )
        )


def get_dummy_job_request_v0(uuid: str) -> V0FacilitatorJobRequest:
    return V0FacilitatorJobRequest(
        type="job.new",
        uuid=uuid,
        miner_hotkey="miner_hotkey",
        executor_class=DEFAULT_EXECUTOR_CLASS,
        docker_image="nvidia",
        raw_script="print('hello world')",
        args=[],
        env={},
        use_gpu=False,
        input_url="fake.com/input",
        output_url="fake.com/output",
    )


def get_dummy_job_request_v1(uuid: str) -> V1FacilitatorJobRequest:
    return V1FacilitatorJobRequest(
        type="job.new",
        uuid=uuid,
        miner_hotkey="miner_hotkey",
        docker_image="nvidia",
        raw_script="print('hello world')",
        args=[],
        env={},
        use_gpu=False,
        volume={
            "volume_type": "multi_volume",
            "volumes": [
                {
                    "volume_type": "single_file",
                    "url": "fake.com/input.txt",
                    "relative_path": "input.txt",
                },
                {
                    "volume_type": "zip_url",
                    "contents": "fake.com/input.zip",
                    "relative_path": "zip/",
                },
            ],
        },
        output_upload={
            "output_upload_type": "multi_upload",
            "uploads": [
                {
                    "output_upload_type": "single_file_post",
                    "url": "http://s3.bucket.com/output1.txt",
                    "relative_path": "output1.txt",
                },
                {
                    "output_upload_type": "single_file_put",
                    "url": "http://s3.bucket.com/output2.zip",
                    "relative_path": "zip/output2.zip",
                },
            ],
            "system_output": {
                "output_upload_type": "zip_and_http_put",
                "url": "http://r2.bucket.com/output.zip",
            },
        },
    )


class MockHyperparameters:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class MockSubtensor:
    def __init__(
        self,
        *args,
        mocked_set_weights=lambda: (True, ""),
        mocked_commit_weights=lambda: (True, ""),
        mocked_reveal_weights=lambda: (True, ""),
        mocked_metagraph=lambda: MockMetagraph(),
        hyperparameters=MockHyperparameters(
            commit_reveal_weights_enabled=False,
            commit_reveal_weights_interval=1000,
            max_weight_limit=65535,
        ),
        block_duration=timedelta(seconds=1),
        override_block_number=None,
        increase_block_number_with_each_call=False,
        block_hash="0xed0050a68f7027abdf10a5e4bd7951c00d886ddbb83bed5b3236ed642082b464",
    ):
        self.mocked_set_weights = mocked_set_weights
        self.mocked_commit_weights = mocked_commit_weights
        self.mocked_reveal_weights = mocked_reveal_weights
        self.mocked_metagraph = mocked_metagraph
        self.hyperparameters = hyperparameters
        self.weights_set: list[list[numbers.Number]] = []
        self.weights_committed: list[list[numbers.Number]] = []
        self.weights_revealed: list[list[numbers.Number]] = []
        self.init_time = monotonic()
        self.block_duration = block_duration
        self.override_block_number = override_block_number
        self.block_hash = block_hash
        self.increase_block_number_with_each_call = increase_block_number_with_each_call
        self.previously_returned_block = None

    def get_block_hash(self, block_id) -> str:
        return self.block_hash

    def min_allowed_weights(self, netuid):
        return 0

    def max_weight_limit(self, netuid):
        return 99999

    def get_subnet_hyperparameters(self, netuid: int) -> MockHyperparameters:
        return self.hyperparameters

    def metagraph(self, netuid, block: int | None = None):
        if block is not None and block < self.get_current_block() - 300:
            raise SubstrateRequestException(
                {
                    "code": -32000,
                    "message": "Client error: UnknownBlock: State already discarded for 0xabc",
                }
            )
        return self.mocked_metagraph()

    def set_weights(
        self,
        wallet,
        netuid,
        uids,
        weights,
        version_key,
        wait_for_inclusion,
        wait_for_finalization,
        **kwargs,
    ) -> tuple[bool, str]:
        if not isinstance(weights, list):
            weights = weights.tolist()
        self.weights_set.append(weights)
        return self.mocked_set_weights()

    def commit_weights(self, weights, **kwargs) -> tuple[bool, str]:
        self.weights_committed.append(weights)
        if self.hyperparameters.commit_reveal_weights_enabled:
            return self.mocked_commit_weights()
        return False, "MockSubtensor doesn't support commit_weights"

    def reveal_weights(self, weights, **kwargs) -> tuple[bool, str]:
        self.weights_revealed.append(weights)
        if self.hyperparameters.commit_reveal_weights_enabled:
            return self.mocked_reveal_weights()
        return False, "MockSubtensor doesn't support reveal_weights"

    def get_current_block(self) -> int:
        if not self.increase_block_number_with_each_call:
            return self._get_block_number()
        if self.previously_returned_block is not None:
            self.previously_returned_block += 1
            return self.previously_returned_block
        self.previously_returned_block = self._get_block_number()
        return self.previously_returned_block

    def _get_block_number(self) -> int:
        if self.override_block_number is not None:
            return self.override_block_number
        return 1000 + int((monotonic() - self.init_time) / self.block_duration.total_seconds())


class MockNeuron:
    def __init__(self, hotkey, uid):
        self.hotkey = hotkey
        self.uid = uid
        self.stake = Balance((uid + 1) * 1001.0)
        self.axon_info = MockedAxonInfo(True, f"127.0.0.{uid}", 4, 8000 + uid)


class MockBlock:
    def item(self) -> int:
        return 1000


class MockMetagraph:
    def __init__(
        self,
        netuid=1,
        num_neurons: int | None = NUM_NEURONS,
        neurons: list[MockNeuron] | None = None,
    ):
        if (neurons is None) == (num_neurons is None):
            raise ValueError("Specify either num_neurons or neurons, exactly one of them")
        if neurons is not None:
            num_neurons = len(neurons)
            self.neurons = neurons
        else:
            self.neurons = [MockNeuron(f"hotkey_{i}", i) for i in range(NUM_NEURONS)]
        self.n = num_neurons
        self.netuid = netuid
        self.num_neurons = num_neurons
        self.W = np.ones((num_neurons, num_neurons))
        self.hotkeys = [f"hotkey_{i}" for i in range(num_neurons)]
        self.uids = np.array(list(range(num_neurons)))
        self.block = MockBlock()


def check_system_events(
    type: SystemEvent.EventType, subtype: SystemEvent.EventSubType, count: int = 1
):
    assert (
        SystemEvent.objects.using(settings.DEFAULT_DB_ALIAS)
        .filter(
            type=type,
            subtype=subtype,
        )
        .count()
        == count
    )


def patch_constance(config_overlay: dict):
    old_getattr = constance.base.Config.__getattr__

    def new_getattr(s, key):
        return config_overlay[key] if key in config_overlay else old_getattr(s, key)

    return mock.patch.object(constance.base.Config, "__getattr__", new_getattr)


class Celery:
    """
    Context manager for starting in test. Able to perform patches before celery start (via hook_script_file_path).
    Able to start celery on a remote host (useful for macs, because workers dying there issue error dialogs). To start
    remotely, use the `REMOTE_HOST`, `REMOTE_VENV` and `REMOTE_CELERY_START_SCRIPT` env vars.
    """

    def __init__(self, hook_script_file_path=None, run_id=None):
        self.celery_script = os.getenv(
            "REMOTE_CELERY_START_SCRIPT",
            (Path(__file__).parents[5] / "dev_env_setup" / "start_celery.sh").as_posix(),
        )
        self.celery_process = None
        self.pid_files_pattern = "/tmp/celery-validator-*.pid"
        self.remote_host = os.getenv("REMOTE_HOST", None)
        self.remote_venv = os.getenv("REMOTE_VENV", None)
        if bool(self.remote_host) != bool(self.remote_venv):
            raise ValueError(
                f"Provide both REMOTE_HOST and REMOTE_VENV or neither. Currently REMOTE_HOST={self.remote_host}, REMOTE_VENV={self.remote_venv}"
            )
        self.hook_script_file_path = hook_script_file_path
        self.run_id = run_id

    def read_pid(self, filename):
        if self.remote_host:
            result = subprocess.run(
                ["ssh", self.remote_host, f"cat {filename}"], stdout=subprocess.PIPE
            )
            pid = int(result.stdout.decode().strip())
        else:
            with open(filename) as f:
                pid = int(f.read().strip())
        return pid

    def get_pid_files(self):
        if self.remote_host:
            result = subprocess.run(
                ["ssh", self.remote_host, f"ls {self.pid_files_pattern}"], stdout=subprocess.PIPE
            )
            return result.stdout.decode().split()
        else:
            return glob.glob(self.pid_files_pattern)

    def kill_pids(self):
        for pid_filename in self.get_pid_files():
            try:
                pid = self.read_pid(pid_filename)
                kill_command = f"kill -9 {pid}"
                if self.remote_host:
                    subprocess.check_call(["ssh", self.remote_host, kill_command])
                else:
                    os.kill(pid, 9)
            except (FileNotFoundError, ValueError, ProcessLookupError):
                continue

    def __enter__(self):
        self.kill_pids()
        test_database_name = shlex.quote(settings.DATABASES["default"]["NAME"])
        if self.remote_host:
            remote_host = shlex.quote(self.remote_host)
            remote_venv = shlex.quote(self.remote_venv)
            hook_script_file_path = shlex.quote(self.hook_script_file_path)
            celery_script = shlex.quote(self.celery_script)

            command = shlex.join(
                [
                    "ssh",
                    remote_host,
                    f"source {remote_venv}/bin/activate && DEBUG_CELERY_HOOK_SCRIPT_FILE={hook_script_file_path or ''} DEBUG_OVERRIDE_DATABASE_NAME={test_database_name} PYTEST_RUN_ID={self.run_id or ''} {celery_script}",
                ]
            )
            self.celery_process = subprocess.Popen(
                command,
                shell=True,
            )
        else:
            env = {
                **os.environ,
                "PYTEST_RUN_ID": self.run_id,
                "DEBUG_OVERRIDE_DATABASE_NAME": test_database_name,
                **(
                    {"DEBUG_CELERY_HOOK_SCRIPT_FILE": self.hook_script_file_path}
                    if self.hook_script_file_path
                    else {}
                ),
            }
            self.celery_process = subprocess.Popen(self.celery_script, shell=True, env=env)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.celery_process.terminate()
            self.celery_process.wait(15)
        except Exception:
            logger.exception("Encountered when killing celery")

        self.kill_pids()

        # Delete the pid files
        for pid_filename in self.get_pid_files():
            try:
                if self.remote_host:
                    subprocess.check_call(["ssh", self.remote_host, f"rm {pid_filename}"])
                else:
                    os.remove(pid_filename)
            except OSError:
                pass
