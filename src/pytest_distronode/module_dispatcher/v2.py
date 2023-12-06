"""Fixme."""
from __future__ import annotations

import warnings

from typing import TYPE_CHECKING

import distronode.constants
import distronode.errors
import distronode.utils

from distronode.cli import CLI
from distronode.executor.task_queue_manager import TaskQueueManager
from distronode.playbook.play import Play
from distronode.plugins.callback import CallbackBase
from distronode.plugins.loader import module_loader

from pytest_distronode.errors import DistronodeConnectionFailure
from pytest_distronode.has_version import has_distronode_v2
from pytest_distronode.module_dispatcher import BaseModuleDispatcher
from pytest_distronode.results import AdHocResult


if not has_distronode_v2:
    msg = "Only supported with distronode-2.* and newer"
    raise ImportError(msg)


class ResultAccumulator(CallbackBase):
    """Fixme."""

    def __init__(self, *args, **kwargs) -> None:
        """Initialize object."""
        super().__init__(*args, **kwargs)
        self.contacted = {}
        self.unreachable = {}

    def v2_runner_on_failed(self, result, *args, **kwargs):
        """Fixme."""
        self.contacted[result._host.get_name()] = result._result

    v2_runner_on_ok = v2_runner_on_failed

    def v2_runner_on_unreachable(self, result):
        """Fixme."""
        self.unreachable[result._host.get_name()] = result._result

    @property
    def results(self):
        """Fixme."""
        return {"contacted": self.contacted, "unreachable": self.unreachable}


class ModuleDispatcherV2(BaseModuleDispatcher):
    """Pass."""

    if TYPE_CHECKING:
        from collections.abc import Sequence

    required_kwargs: Sequence[str] = (
        "inventory",
        "inventory_manager",
        "variable_manager",
        "host_pattern",
        "loader",
    )

    def has_module(self, name):
        """Fixme."""
        # Make sure we parse module_path and pass it to the loader,
        # otherwise, only built-in modules will work.
        if "module_path" in self.options:
            paths = self.options["module_path"]
            if isinstance(paths, (list, tuple, set)):
                for path in paths:
                    module_loader.add_directory(path)
            else:
                module_loader.add_directory(paths)

        return module_loader.has_plugin(name)

    def _run(self, *module_args, **complex_args):
        """Execute an distronode adhoc command returning the result in a AdhocResult object."""
        # Assemble module argument string
        if module_args:
            complex_args.update({"_raw_params": " ".join(module_args)})

        # Assert hosts matching the provided pattern exist
        hosts = self.options["inventory_manager"].list_hosts()
        no_hosts = False
        if len(hosts) == 0:
            no_hosts = True
            warnings.warn("provided hosts list is empty, only localhost is available")

        self.options["inventory_manager"].subset(self.options.get("subset"))
        hosts = self.options["inventory_manager"].list_hosts(
            self.options["host_pattern"],
        )
        if len(hosts) == 0 and not no_hosts:
            msg = "Specified hosts and/or --limit does not match any hosts"
            raise distronode.errors.DistronodeError(
                msg,
            )

        # pylint: disable=no-member
        parser = CLI.base_parser(
            runas_opts=True,
            inventory_opts=True,
            async_opts=True,
            output_opts=True,
            connect_opts=True,
            check_opts=True,
            runtask_opts=True,
            vault_opts=True,
            fork_opts=True,
            module_opts=True,
        )
        (options) = parser.parse_args([])

        # Pass along cli options
        options.verbosity = 5
        options.connection = self.options.get("connection")
        options.remote_user = self.options.get("user")
        options.become = self.options.get("become")
        options.become_method = self.options.get("become_method")
        options.become_user = self.options.get("become_user")
        options.module_path = self.options.get("module_path")

        # Initialize callback to capture module JSON responses
        callback = ResultAccumulator()

        kwargs = {
            "inventory": self.options["inventory_manager"],
            "variable_manager": self.options["variable_manager"],
            "loader": self.options["loader"],
            "options": options,
            "stdout_callback": callback,
            "passwords": {"conn_pass": None, "become_pass": None},
        }

        # create a pseudo-play to execute the specified module via a single task
        play_ds = {
            "name": "pytest-distronode",
            "hosts": self.options["host_pattern"],
            "gather_facts": "no",
            "tasks": [
                {
                    "action": {
                        "module": self.options["module_name"],
                        "args": complex_args,
                    },
                },
            ],
        }

        play = Play().load(
            play_ds,
            variable_manager=self.options["variable_manager"],
            loader=self.options["loader"],
        )

        # now create a task queue manager to execute the play
        tqm = None
        try:
            tqm = TaskQueueManager(**kwargs)
            tqm.run(play)
        finally:
            if tqm:
                tqm.cleanup()

        # Raise exception if host(s) unreachable
        if callback.unreachable:
            msg = "Host unreachable"
            raise DistronodeConnectionFailure(
                msg,
                dark=callback.unreachable,
                contacted=callback.contacted,
            )

        # Success!
        return AdHocResult(contacted=callback.contacted)
