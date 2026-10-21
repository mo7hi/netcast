# Copyright (c) 2026, NetCast Contributors
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""Pluggy-based plugin manager for NetCast daemon.

Manages registration of hook specifications, built-in plugins, and third-party
entry point plugins. Wraps all hook executions in protective error handlers so
a faulty plugin cannot crash the daemon.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import logging
from collections.abc import Mapping, Sequence
from typing import Any

import pluggy

from daemon.hooks import PROJECT_NAME, NetCastHookSpecs

logger = logging.getLogger("netcast.plugins")


class NetCastPluginManager(pluggy.PluginManager):
    """Specialized PluginManager for NetCast with resilient hook execution."""

    def __init__(self, project_name: str = PROJECT_NAME) -> None:
        """Initialize the plugin manager and register standard hook specifications.

        :param project_name: Namespace identifier for pluggy plugins.
        """
        super().__init__(project_name)
        self.add_hookspecs(NetCastHookSpecs)
        logger.debug("Initialized NetCastPluginManager with namespace %r", project_name)

    def _hookexec(
        self,
        hook_name: str,
        methods: Sequence[pluggy.HookImpl],
        kwargs: Mapping[str, Any],
        firstresult: bool,
    ) -> Any:
        """Execute hook implementations with per-plugin exception isolation.

        Ensures that if any individual plugin raises an exception, the failure
        is logged with full context and traceback, while allowing all other
        registered plugins to execute without interrupting daemon operation.

        :param hook_name: Name of the hook being invoked.
        :param methods: Ordered sequence of hook implementation objects.
        :param kwargs: Keyword arguments passed to the hook implementations.
        :param firstresult: If True, stop and return the first non-None result.
        :return: Aggregated list of results, or first result if firstresult=True.
        """
        results: list[Any] = []
        for method in methods:
            try:
                res = self._inner_hookexec(hook_name, [method], kwargs, firstresult)
                if firstresult:
                    if res is not None:
                        return res
                else:
                    if isinstance(res, list):
                        results.extend(res)
                    elif res is not None:
                        results.append(res)
            except Exception as exc:
                plugin_name = getattr(method, "plugin_name", None) or getattr(
                    getattr(method, "plugin", None), "__name__", "unknown"
                )
                logger.error(
                    "Faulty plugin '%s' raised an exception while executing hook '%s': %s",
                    plugin_name,
                    hook_name,
                    exc,
                    exc_info=True,
                )
        return None if firstresult else results

    def load_builtin_plugins(self) -> int:
        """Attempt to load built-in plugins by module path.

        :return: Count of successfully loaded built-in plugins.
        """
        builtins: list[tuple[str, str, str]] = [
            ("fair_share", "daemon.builtin_plugins.fair_share", "FairSharePlugin"),
            ("captive_portal", "daemon.builtin_plugins.captive_portal", "CaptivePortalPlugin"),
            ("qr_connect", "daemon.builtin_plugins.qr_connect", "QRConnectPlugin"),
            ("status_monitor", "daemon.builtin_plugins.status_monitor", "StatusMonitorPlugin"),
        ]

        loaded = 0
        for name, mod_path, class_name in builtins:
            if self.is_registered(name) or self.get_plugin(name) is not None:
                continue
            try:
                mod = importlib.import_module(mod_path)
                cls = getattr(mod, class_name)
                instance = cls() if isinstance(cls, type) else cls
                self.register(instance, name=name)
                loaded += 1
                logger.info("Loaded built-in plugin '%s' (%s.%s)", name, mod_path, class_name)
            except ModuleNotFoundError:
                logger.debug("Built-in plugin module '%s' not present, skipping", mod_path)
            except Exception as exc:
                logger.error(
                    "Failed to initialize built-in plugin '%s' (%s): %s",
                    name,
                    mod_path,
                    exc,
                    exc_info=True,
                )
        return loaded

    def load_entrypoint_plugins(self) -> int:
        """Discover and load external third-party plugins declared via entry points.

        :return: Count of successfully loaded entry point plugins.
        """
        loaded = 0
        try:
            entry_points = importlib.metadata.entry_points(group=self.project_name)
        except Exception as exc:
            logger.error("Failed to query entry points for group %r: %s", self.project_name, exc)
            return 0

        for ep in entry_points:
            plugin_name = ep.name
            if self.is_registered(plugin_name) or self.get_plugin(plugin_name) is not None:
                continue
            try:
                target = ep.load()
                instance = target() if isinstance(target, type) else target
                self.register(instance, name=plugin_name)
                loaded += 1
                logger.info(
                    "Successfully loaded plugin '%s' from entry point '%s'",
                    plugin_name,
                    ep.value,
                )
            except ModuleNotFoundError as exc:
                logger.debug(
                    "Plugin '%s' entry point module could not be imported: %s",
                    plugin_name,
                    exc,
                )
            except Exception as exc:
                logger.error(
                    "Failed to load entry point plugin '%s' (%s): %s",
                    plugin_name,
                    ep.value,
                    exc,
                    exc_info=True,
                )
        return loaded


def get_plugin_manager() -> NetCastPluginManager:
    """Create, configure, and initialize the singleton NetCast plugin manager.

    Registers all hook specifications, discovers built-in plugins, and loads
    external plugins registered via Python package entry points.

    :return: Fully initialized NetCastPluginManager instance.
    """
    pm = NetCastPluginManager(PROJECT_NAME)
    pm.load_builtin_plugins()
    pm.load_entrypoint_plugins()
    return pm
