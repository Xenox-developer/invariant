"""Validated project policy shared by launch, configuration and inspection."""
import copy
from decimal import Decimal
import math

PROFILE = {"schema": 1, "profile": "codex-workspace-v1", "agent": "codex",
           "model_access": "openai", "command_network": "none"}
DEFAULT_DURATION = 900
DEFAULT_CONFIG = {**PROFILE, "resource_mode": "manual", "workspace_access": "read-write", "duration_seconds": DEFAULT_DURATION,
                  "limits": {"cpus": 1, "memory_mib": 512, "extra_swap_mib": 0,
                             "processes": 128, "file_descriptors": 256, "scratch_mib": 128}}
# Finite supported bounds; zero never means unlimited.
BOUNDS = {"memory_mib": (64, 65536), "extra_swap_mib": (0, 65536),
          "processes": (32, 4096), "file_descriptors": (64, 65536), "scratch_mib": (16, 16384)}


def normalize(value):
    """Accept legacy fixed profiles and optional settings, reject unknown authority."""
    if not isinstance(value, dict) or set(value) - set(DEFAULT_CONFIG):
        raise RuntimeError("Unsupported workspace policy key; see invariant config")
    for key, expected in PROFILE.items():
        if key == "command_network":
            continue
        if key == 'profile' and value.get(key) == 'codex-environment-v2':
            continue
        if key not in value or type(value[key]) is not type(expected) or value[key] != expected:
            raise RuntimeError(f"Unsupported workspace policy: {key} must remain {expected!r}")
    result = copy.deepcopy(DEFAULT_CONFIG)
    result['profile'] = value['profile']
    result.update({key: value[key] for key in ("workspace_access", "duration_seconds", "command_network", "resource_mode") if key in value})
    if result["command_network"] not in ("none", "internet"):
        raise RuntimeError("Unsupported command_network; use none or internet")
    if result["workspace_access"] not in ("read-write", "read-only"):
        raise RuntimeError("workspace_access must be read-write or read-only")
    if result["resource_mode"] not in ("auto", "manual"):
        raise RuntimeError("resource_mode must be auto or manual")
    duration = result["duration_seconds"]
    if type(duration) is not int or not 1 <= duration <= 86400:
        raise RuntimeError("duration_seconds must be an integer between 1 and 86400")
    limits = value.get("limits", {})
    if not isinstance(limits, dict) or set(limits) - set(DEFAULT_CONFIG["limits"]):
        raise RuntimeError("Unsupported limits key; see invariant config")
    result["limits"].update(limits)
    cpus = result["limits"]["cpus"]
    if (type(cpus) not in (int, float) or not 0.1 <= cpus <= 64 or not math.isfinite(cpus)
            or Decimal(str(cpus)) * 100 != (Decimal(str(cpus)) * 100).to_integral_value()):
        raise RuntimeError("limits.cpus must be between 0.1 and 64, with at most two decimal places")
    for key, (low, high) in BOUNDS.items():
        number = result["limits"][key]
        if type(number) is not int or not low <= number <= high:
            raise RuntimeError(f"limits.{key} must be an integer between {low} and {high}")
    if result["resource_mode"] == "auto" and result["limits"] != DEFAULT_CONFIG["limits"]:
        raise RuntimeError("Automatic resources cannot include custom limits. Use resource_mode manual, or invariant config set limits.KEY VALUE")
    return result


NEW_PROJECT_CONFIG = {**DEFAULT_CONFIG, "profile": "codex-environment-v2", "resource_mode": "auto", "duration_seconds": 3600}


def resource_limits(policy):
    limits = normalize(policy)["limits"]
    return {"cpus": limits["cpus"], "memory_bytes": limits["memory_mib"] * 1024 * 1024,
            "extra_swap_bytes": limits["extra_swap_mib"] * 1024 * 1024,
            "processes": limits["processes"], "file_descriptors": limits["file_descriptors"],
            "scratch_bytes": limits["scratch_mib"] * 1024 * 1024}


LIMITS = resource_limits(DEFAULT_CONFIG)  # Legacy callers/tests use the default profile.


def scratch_options(policy=None):
    limits = resource_limits(policy or DEFAULT_CONFIG)
    execution = ',exec' if policy is not None and policy['profile'] == 'codex-environment-v2' else ''
    return f"rw,nosuid,nodev{execution},size={limits['scratch_bytes']},mode=1777"


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate configuration key: " + key)
        result[key] = value
    return result


def docker_network(policy):
    return "bridge" if normalize(policy)["command_network"] == "internet" else "none"


def seccomp_filename(policy):
    if normalize(policy)['profile'] == 'codex-environment-v2':
        return 'seccomp-environment-internet.json' if policy['command_network'] == 'internet' else 'seccomp-environment.json'
    return "seccomp-internet.json" if normalize(policy)["command_network"] == "internet" else "seccomp.json"


def network_instructions(policy):
    if normalize(policy)["command_network"] == "none":
        return "Command networking is disabled. "
    return "Command internet access is enabled. "
