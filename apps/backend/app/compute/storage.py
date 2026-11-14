"""Storage placement: what a Pod mounts, where, and what that permits.

Why this is a module and not a constant
---------------------------------------
The model can live in three genuinely different places, and they are not interchangeable:

* **regional network volume** — one datacenter, full POSIX semantics, the model, the llama.cpp
  runtime and the launch scripts all on one mount. This is what production runs and what has been
  proven with a real GPU answer.
* **global volume** — region-independent, object-backed. The provider's public API can attach one
  to GPU *and* CPU Pods (`mounts.global`, no datacenter constraint) since the 1.2.7 review, so
  "which datacenter" stops being a property of the storage. But it is not a filesystem: no file
  locks, no atomic rename, no hard links, no permission bits, eventual consistency, and a small-file
  tree is slow to open. Loading a GGUF read-only from it is *plausible*, not proven: mmap behaviour
  on the object-backed layer has to be measured on a real Pod before anything claims it works.
* **local model copy** — weights fetched into the container disk before inference starts. It costs
  a copy on every start but never depends on the storage's file semantics.

The rest of the product already understands `storage_type` on a placement
(``app.compute.candidates``). What it lacked was the second half: turning an attachment set into
the actual mount list and mount path, deriving the model/runtime paths from that mount instead of
hardcoding ``/workspace``, and refusing a combination *before* a Pod is created. That is this file.

Everything here is pure: no I/O, no credential, no provider call. The caller supplies the facts
(which volumes exist, how large the model is, how much local disk is free) and gets one typed
verdict it can put in an audit trail.

Mount paths are the provider's documented behaviour, not a guess: a global volume mounts at
``/workspace``, or at ``/workspace-global`` when a network volume is also attached, and the network
volume moves to ``/workspace-2`` only when the operator edits the global mount path explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .candidates import GLOBAL_VOLUME, REGIONAL_VOLUME

# The fourth value is not a storage at all: it is the typed answer when the requested combination
# cannot be mounted or is not a kind this build knows. Never silently treated as "regional".
LOCAL_MODEL_COPY = "local_model_copy"
UNSUPPORTED_STORAGE = "unsupported_storage"
STORAGE_MODES = (REGIONAL_VOLUME, GLOBAL_VOLUME, LOCAL_MODEL_COPY)
KNOWN_MODES = STORAGE_MODES + (UNSUPPORTED_STORAGE,)

# The provider's own mount defaults (docs: "Global volumes for Pods" → Mount paths).
DEFAULT_MOUNT = "/workspace"
GLOBAL_SECOND_MOUNT = "/workspace-global"
NETWORK_SECOND_MOUNT = "/workspace-2"
LOCAL_MODEL_ROOT = "/models"

# Paths *inside* the model root. They are relative on purpose: the absolute path is derived, so a
# non-``/workspace`` mount cannot silently point llama.cpp at a file that is not there.
MODEL_RELATIVE_PATH = "models/orcarouter-qwen38/orcarouter_Qwen3.8-27B-Uncensored-Q5_K_M.gguf"
RUNTIME_RELATIVE_PATH = "llama.cpp/build/bin/llama-server"
START_SCRIPT = "start-llm.sh"
CHECK_SCRIPT = "check-llm.sh"

# Verdicts. Each one is a code the operator reads, never a sentence.
READY = "ready"
NO_STORAGE = "no_storage"
MODEL_NOT_FOUND = "model_not_found"
AMBIGUOUS_MODEL_SOURCE = "ambiguous_model_source"
MOUNT_PATH_CONFLICT = "mount_path_conflict"
MULTIPLE_GLOBAL_VOLUMES = "multiple_global_volumes"
UNSUPPORTED_KIND = "unsupported_storage_kind"
STORAGE_INCOMPATIBLE_RUNTIME = "storage_incompatible_runtime"
STORAGE_UNVERIFIED = "storage_unverified"
INSUFFICIENT_LOCAL_DISK = "insufficient_local_disk"

# The reason codes inside a global-volume verdict. They are the checks that would have to pass on a
# real Pod before "global volume" could be called compatible.
GLOBAL_UNVERIFIED_REASONS = (
    "mmap_on_object_storage_unmeasured",
    "startup_latency_unmeasured",
    "eventual_consistency",
)

# A copy needs the model plus room for the OS, llama.cpp and the container's own writes. Ten
# percent is a reserve, not a measurement.
LOCAL_COPY_RESERVE_FRACTION = Decimal("0.10")

# Measured on this account (GET /v2/billing/network-volumes, 30 days, two STANDARD 50 GB volumes):
# $0.11666667 per volume per day = $3.50/month = $0.07 per GB-month. It is a *measured* rate, so a
# projection built on it is honest; a rate the provider has not published is never invented.
NETWORK_STORAGE_PER_GB_MONTH = Decimal("0.07")
# Documented request rates for global volumes (storage/globalvolume/overview → Request costs).
GLOBAL_CLASS_A_PER_1000 = Decimal("0.005")
GLOBAL_CLASS_B_PER_1000 = Decimal("0.0005")
# The provider documents that global volumes charge for storage *and* requests, but has not
# published the per-GB rate on the pages this build reads. Callers must pass a rate they can point
# at, and a projection must say which rate it used.
GLOBAL_STORAGE_RATE_PUBLISHED = False


@dataclass(frozen=True)
class Attachment:
    """One volume this build wants on the Pod, before mount paths are resolved.

    ``has_model`` marks the volume the GGUF actually lives on. Exactly one attachment should carry
    it: the mount that holds the model is the one llama.cpp is pointed at, so "which one is it"
    must be a fact, not an ordering coincidence.
    """

    mode: str
    volume_id: str = ""
    mount_path: str = ""
    has_model: bool = False
    has_runtime: bool = False
    label: str = ""

    @property
    def known(self) -> bool:
        return self.mode in STORAGE_MODES

    def audit(self) -> dict:
        return {
            "mode": self.mode,
            "volume_id": self.volume_id,
            "mount_path": self.mount_path,
            "has_model": bool(self.has_model),
            "has_runtime": bool(self.has_runtime),
            "label": self.label,
        }


@dataclass(frozen=True)
class Mount:
    """One entry of the Pod's ``mounts`` document."""

    kind: str  # "network" or "global"
    volume_id: str
    path: str

    def audit(self) -> dict:
        return {"kind": self.kind, "volume_id": self.volume_id, "path": self.path}


@dataclass(frozen=True)
class MountPlan:
    """The mount list, the model root, and the typed conclusion when it is not usable."""

    verdict: str
    mounts: tuple[Mount, ...] = ()
    model_root: str = ""
    model_path: str = ""
    runtime_path: str = ""
    reason: str = ""
    storage_mode: str = ""

    def __bool__(self) -> bool:
        return self.verdict == READY

    def audit(self) -> dict:
        return {
            "verdict": self.verdict,
            "storage_mode": self.storage_mode,
            "mounts": [mount.audit() for mount in self.mounts],
            "model_root": self.model_root,
            "model_path": self.model_path,
            "runtime_path": self.runtime_path,
            "reason": self.reason,
        }


def classify(mode: str) -> str:
    """The known mode, or the typed unsupported value. Never a silent fallback to regional.

    ``candidates.Placement.pins_datacenter`` deliberately fails *closed* for an unknown kind (it
    behaves like a regional volume, which cannot widen scheduling). That is the safe allocation
    behaviour, but it is not a storage verdict: a Pod created for an unknown kind would mount the
    wrong thing. Here the unknown kind is refused out loud instead.
    """
    return mode if mode in STORAGE_MODES else UNSUPPORTED_STORAGE


def resolve(attachments) -> MountPlan:
    """One attachment set → the exact mounts, mount root and paths a Pod would use.

    The order of the checks is the order of danger: an unknown kind or a missing volume is refused
    before any path is invented; two global volumes are refused because the provider allows one;
    an ambiguous model source is refused because pointing llama.cpp at the wrong mount is worse
    than not starting.
    """
    items = tuple(attachments or ())
    if not items:
        return MountPlan(NO_STORAGE, reason="no_volume_requested")

    for item in items:
        if not item.known:
            return MountPlan(UNSUPPORTED_KIND, storage_mode=item.mode, reason=item.mode)
        if item.mode != LOCAL_MODEL_COPY and not item.volume_id:
            return MountPlan(NO_STORAGE, storage_mode=item.mode, reason="volume_id_missing")

    globals_ = [item for item in items if item.mode == GLOBAL_VOLUME]
    regionals = [item for item in items if item.mode == REGIONAL_VOLUME]
    locals_ = [item for item in items if item.mode == LOCAL_MODEL_COPY]
    if len(globals_) > 1:
        return MountPlan(MULTIPLE_GLOBAL_VOLUMES, storage_mode=GLOBAL_VOLUME, reason="one_global_max")
    if len(locals_) > 1:
        return MountPlan(MOUNT_PATH_CONFLICT, storage_mode=LOCAL_MODEL_COPY, reason="one_local_max")
    if locals_ and (globals_ or regionals):
        # A local copy is a copy *of* something; combining it with a mounted model root would mean
        # two model sources. Refuse rather than choose.
        return MountPlan(AMBIGUOUS_MODEL_SOURCE, storage_mode=LOCAL_MODEL_COPY)

    path_of: dict[int, str] = {}
    if globals_:
        global_item = globals_[0]
        if global_item.mount_path:
            path_of[id(global_item)] = global_item.mount_path
        elif regionals:
            path_of[id(global_item)] = GLOBAL_SECOND_MOUNT
        else:
            path_of[id(global_item)] = DEFAULT_MOUNT
    for item in regionals:
        if item.mount_path:
            path_of[id(item)] = item.mount_path
        elif DEFAULT_MOUNT in path_of.values():
            path_of[id(item)] = NETWORK_SECOND_MOUNT
        else:
            path_of[id(item)] = DEFAULT_MOUNT
    for item in locals_:
        path_of[id(item)] = item.mount_path or LOCAL_MODEL_ROOT

    used = list(path_of.values())
    if len(set(used)) != len(used):
        return MountPlan(MOUNT_PATH_CONFLICT, storage_mode=_mode_of(items), reason="duplicate_mount_path")
    for path in used:
        if not path.startswith("/") or path.rstrip("/") != path:
            return MountPlan(MOUNT_PATH_CONFLICT, storage_mode=_mode_of(items), reason="relative_or_trailing")

    sources = [item for item in items if item.has_model]
    if not sources:
        return MountPlan(MODEL_NOT_FOUND, storage_mode=_mode_of(items), reason="no_model_source")
    if len(sources) > 1:
        return MountPlan(AMBIGUOUS_MODEL_SOURCE, storage_mode=_mode_of(items), reason="two_model_sources")
    model_item = sources[0]
    root = path_of[id(model_item)]

    mounts = tuple(
        Mount(
            kind="global" if item.mode == GLOBAL_VOLUME else "network",
            volume_id=item.volume_id,
            path=path_of[id(item)],
        )
        for item in items
        if item.mode in (GLOBAL_VOLUME, REGIONAL_VOLUME)
    )
    return MountPlan(
        READY,
        mounts=mounts,
        model_root=root,
        model_path=join(root, MODEL_RELATIVE_PATH),
        runtime_path=join(root, RUNTIME_RELATIVE_PATH),
        storage_mode=model_item.mode,
    )


def join(root: str, relative: str) -> str:
    """One POSIX path from a mount and a relative path, with no double or missing slash."""
    if not root:
        return ""
    return root.rstrip("/") + "/" + relative.lstrip("/")


def check_compatibility(
    plan: MountPlan,
    *,
    runtime_on_storage: bool = True,
    writes_to_storage: bool = True,
    model_bytes: int = 0,
    local_free_bytes: int | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Whether llama.cpp may be pointed at this plan, as one verdict plus its reasons.

    The three inputs are facts the caller must know about its own runtime, not guesses made here:

    * ``runtime_on_storage`` — is ``llama-server`` (and its launch scripts) on the mounted volume,
      or on the container disk?
    * ``writes_to_storage`` — does the launch path write anything (a log, a pid file, a cache) to
      the mount? The measured ``start-llm.sh`` does write its log and pid there, which is fine on a
      network volume and a bad idea on an object-backed one.
    * ``local_free_bytes`` / ``model_bytes`` — the container disk a local copy would consume.

    A regional volume is the proven path and is ``READY``. A global volume is never called
    compatible from static checks alone: there is no way to prove mmap or startup latency without a
    real Pod, so the honest verdict is ``storage_unverified`` — the checks that must pass are named
    in ``GLOBAL_UNVERIFIED_REASONS``.
    """
    if not plan:
        return plan.verdict, (plan.reason or plan.verdict,)

    if plan.storage_mode == REGIONAL_VOLUME:
        return READY, ()

    if plan.storage_mode == LOCAL_MODEL_COPY:
        if local_free_bytes is None or model_bytes <= 0:
            return STORAGE_UNVERIFIED, ("local_disk_unmeasured",)
        reserve = int(Decimal(model_bytes) * LOCAL_COPY_RESERVE_FRACTION)
        if local_free_bytes < model_bytes + reserve:
            return INSUFFICIENT_LOCAL_DISK, (
                f"need={model_bytes + reserve}",
                f"free={local_free_bytes}",
            )
        return READY, ()

    if plan.storage_mode == GLOBAL_VOLUME:
        if writes_to_storage:
            return STORAGE_INCOMPATIBLE_RUNTIME, ("launch_path_writes_to_object_storage",)
        reasons = list(GLOBAL_UNVERIFIED_REASONS)
        if runtime_on_storage:
            reasons.append("runtime_small_file_tree_on_object_storage")
        return STORAGE_UNVERIFIED, tuple(reasons)

    return UNSUPPORTED_KIND, (plan.storage_mode or "unknown",)


def monthly_storage_cost(gb: float | int | Decimal, rate_per_gb_month: Decimal) -> Decimal:
    """GB-month cost at a rate the caller can point at. No default rate is assumed."""
    if not isinstance(rate_per_gb_month, Decimal):
        rate_per_gb_month = Decimal(str(rate_per_gb_month))
    return (Decimal(str(gb)) * rate_per_gb_month).quantize(Decimal("0.0001"))


def global_request_cost(class_a: int = 0, class_b: int = 0) -> Decimal:
    """Documented global-volume request cost: writes/creates/lists vs reads/metadata."""
    cost = (
        Decimal(int(class_a)) * GLOBAL_CLASS_A_PER_1000 + Decimal(int(class_b)) * GLOBAL_CLASS_B_PER_1000
    ) / Decimal(1000)
    return cost.quantize(Decimal("0.000001"))


def migration_projection(model_bytes: int, *, files: int, runtime_bytes: int = 0) -> dict:
    """A *bounded* transfer projection, never a pretend measurement.

    The provider bills global-volume requests per operation, and how many operations one ``rsync``
    turns into depends on block size, readahead and write-back buffering. So the honest output is a
    floor (one write and one read, i.e. whole-file copies with large buffering) and a per-file
    ceiling (a metadata read plus a write for each object). The measured number can only come from
    running the copy.
    """
    total_bytes = int(model_bytes) + int(runtime_bytes)
    per_file_a, per_file_b = int(files), int(files)
    return {
        "bytes": total_bytes,
        "files": int(files),
        "class_a_requests": {"floor": 1, "per_file": per_file_a},
        "class_b_requests": {"floor": 1, "per_file": per_file_b},
        "request_cost_usd": {
            "floor": str(global_request_cost(1, 1)),
            "per_file": str(global_request_cost(per_file_a, per_file_b)),
        },
        "note": "rates are the provider's documented Class A/B prices; storage rate must be supplied",
    }


def _mode_of(items) -> str:
    modes = {item.mode for item in items}
    return modes.pop() if len(modes) == 1 else GLOBAL_VOLUME if GLOBAL_VOLUME in modes else REGIONAL_VOLUME
