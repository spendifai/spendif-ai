#!/usr/bin/env bash
# ── run_on_runpod.sh ──────────────────────────────────────────────────────────
# Launch the Spendify benchmark on a RunPod GPU pod and pull results back.
#
# AI-99: the existing `docker/Dockerfile.benchmark` (CUDA 13.2.1) already
# does everything we need headless. This script just:
#   1) builds the image locally
#   2) pushes to a registry (ghcr.io / Docker Hub — your choice)
#   3) starts a Pod on RunPod (RTX 4090 community by default, ~$0.34/h)
#   4) waits for the entrypoint to finish
#   5) rsyncs results from the pod to ./benchmark/results/
#   6) terminates the pod
#
# Cost reference: a full AI-92 sweep (5 models × 5 files) takes ~1 h on a
# 4090 community pod → about $0.34 per run.
#
# Prereqs (one-time setup):
#   * docker login <your-registry>          # ghcr.io or docker.io
#   * pip install runpodctl                 # or: brew install runpodctl
#   * runpodctl config --apiKey $RUNPOD_API_KEY
#
# Usage:
#   benchmark/run_on_runpod.sh \
#       --image ghcr.io/spendifai/spendif-ai-bench:latest \
#       --gpu "NVIDIA GeForce RTX 4090" \
#       --models "qwen2.5-3b,qwen3.5-9b-q3" \
#       --runs 1 \
#       --files 50
#
# Environment overrides (alternative to flags):
#   IMAGE=… GPU=… MODELS=… RUNS=… FILES=… ./benchmark/run_on_runpod.sh

set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
IMAGE="${IMAGE:-ghcr.io/${GITHUB_USER:-$USER}/spendify-bench:latest}"
GPU="${GPU:-NVIDIA GeForce RTX 4090}"
MODELS="${MODELS:-qwen2.5-1.5b,qwen2.5-3b,qwen3.5-9b-q3}"
RUNS="${RUNS:-1}"
FILES="${FILES:-50}"
SKIP_BUILD="${SKIP_BUILD:-0}"
SKIP_PUSH="${SKIP_PUSH:-0}"
CLOUD_TYPE="${CLOUD_TYPE:-COMMUNITY}"
DISK_GB="${DISK_GB:-15}"
LOCAL_RESULTS_DIR="$(cd "$(dirname "$0")/.." && pwd)/benchmark/results"

# ── Parse flags ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --image)         IMAGE="$2";       shift 2 ;;
        --gpu)           GPU="$2";         shift 2 ;;
        --models)        MODELS="$2";      shift 2 ;;
        --runs)          RUNS="$2";        shift 2 ;;
        --files)         FILES="$2";       shift 2 ;;
        --skip-build)    SKIP_BUILD=1;     shift ;;
        --skip-push)     SKIP_PUSH=1;      shift ;;
        --cloud-type)    CLOUD_TYPE="$2";  shift 2 ;;
        --disk-gb)       DISK_GB="$2";     shift 2 ;;
        --results-dir)   LOCAL_RESULTS_DIR="$2"; shift 2 ;;
        -h|--help)
            sed -n '/^# ─/,/^$/p' "$0" | sed 's/^# \?//'
            exit 0 ;;
        *) echo "Unknown flag: $1" >&2; exit 1 ;;
    esac
done

# ── Sanity checks ────────────────────────────────────────────────────────────
command -v docker     >/dev/null || { echo "docker missing"; exit 1; }
command -v runpodctl  >/dev/null || { echo "runpodctl missing — install via 'brew install runpodctl' or pip install runpodctl"; exit 1; }
[[ -n "${RUNPOD_API_KEY:-}" ]] || { echo "Set RUNPOD_API_KEY env var (or runpodctl config --apiKey …)"; exit 1; }

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "[runpod] image       : $IMAGE"
echo "[runpod] gpu         : $GPU"
echo "[runpod] models      : $MODELS"
echo "[runpod] runs        : $RUNS"
echo "[runpod] files       : $FILES"
echo "[runpod] results to  : $LOCAL_RESULTS_DIR"
echo "[runpod] build       : $([[ $SKIP_BUILD -eq 1 ]] && echo SKIP || echo YES)"
echo "[runpod] push        : $([[ $SKIP_PUSH  -eq 1 ]] && echo SKIP || echo YES)"
echo

# ── 1. Build (optional) ──────────────────────────────────────────────────────
if [[ $SKIP_BUILD -ne 1 ]]; then
    echo "[1/5] Building $IMAGE …"
    docker build \
        --platform linux/amd64 \
        -f "$ROOT/docker/Dockerfile.benchmark" \
        -t "$IMAGE" \
        "$ROOT"
fi

# ── 2. Push (optional) ───────────────────────────────────────────────────────
if [[ $SKIP_PUSH -ne 1 ]]; then
    echo "[2/5] Pushing $IMAGE …"
    docker push "$IMAGE"
fi

# ── 3. Launch the pod ────────────────────────────────────────────────────────
echo "[3/5] Creating RunPod pod ($GPU community-cloud)…"
POD_NAME="spendify-bench-$(date +%s)"
# runpodctl notes:
#  * The new subcommand layout is `runpodctl pod create` (the legacy
#    `runpodctl create pod` is deprecated and warns at every call).
#  * `--communityCloud` opts into the cheap community cloud (~$0.34/h
#    for an RTX 4090); the alternative is `--secureCloud` (presence-only).
#  * JSON output is the default — no `--json` flag needed.
#  * Disk knobs trimmed: containerDisk 15 GB is more than enough for
#    the image (~3 GB), the venv (~1 GB), and a single 5-GB model; the
#    persistent volume drops to 1 GB (default) because the smoke test
#    doesn't reuse models across pods. If the community cloud is at
#    capacity for the requested GPU, RunPod returns "This machine does
#    not have the resources to deploy your pod" — try a different GPU
#    via --gpu or wait a few minutes.
# NB: runpodctl recently rewrote the flag layout from camelCase to
# kebab-case and renamed many flags:
#   --imageName         → --image
#   --gpuType           → --gpu-id            (still takes the GPU display
#                                              name, e.g. "NVIDIA GeForce
#                                              RTX 4090", confirmed against
#                                              the public catalogue)
#   --gpuCount          → --gpu-count
#   --containerDiskSize → --container-disk-in-gb
#   --communityCloud    → --cloud-type COMMUNITY
#   --args              → --docker-args
# Capture the raw JSON so we can also surface it on error.
_CREATE_JSON="$(runpodctl pod create \
    --name                 "$POD_NAME" \
    --image                "$IMAGE" \
    --gpu-id               "$GPU" \
    --gpu-count            1 \
    --container-disk-in-gb "$DISK_GB" \
    --cloud-type           "$CLOUD_TYPE" \
    --docker-args          "--models $MODELS --runs $RUNS --files $FILES")"

# The old runpodctl (camelCase flags) returned `{"pod":{"id":"..."}}`.
# The new one (kebab-case flags) returns either `{"id":"..."}` or a
# variant with `podId`. Try all three shapes so the wrapper survives
# future schema bumps. On failure, echo the JSON to stderr so the user
# can see what the CLI actually returned.
POD_ID="$(echo "$_CREATE_JSON" | python3 -c '
import json, sys
data = json.load(sys.stdin)
candidates = [
    (data.get("pod") or {}).get("id"),
    data.get("id"),
    data.get("podId"),
    (data.get("data") or {}).get("id"),
]
pid = next((c for c in candidates if c), None)
if not pid:
    print(json.dumps(data, indent=2), file=sys.stderr)
    sys.exit("could not find pod id in runpodctl response (see stderr)")
print(pid)
')" || { echo "[3/5]   raw JSON above. clean up any orphan with: runpodctl pod list, then runpodctl pod stop <id> + remove <id>" >&2; exit 1; }

echo "[3/5]   pod id: $POD_ID"
trap 'echo "[cleanup] stopping pod $POD_ID …"; runpodctl pod stop "$POD_ID" 2>/dev/null || true; runpodctl pod remove "$POD_ID" 2>/dev/null || true' EXIT INT TERM

# ── 4. Wait for completion ───────────────────────────────────────────────────
echo "[4/5] Waiting for pod to reach EXITED / SUCCEEDED …"
while true; do
    STATUS="$(runpodctl pod get "$POD_ID" | python3 -c '
import sys, json
data = json.load(sys.stdin)
for key in ("desiredStatus", "status"):
    for source in (data.get("pod"), data, data.get("data")):
        if isinstance(source, dict) and source.get(key):
            print(source[key]); sys.exit()
print("UNKNOWN")
' 2>/dev/null || echo UNKNOWN)"
    case "$STATUS" in
        EXITED|SUCCEEDED) echo "[4/5]   pod completed"; break ;;
        FAILED|TERMINATED) echo "[4/5]   pod failed: $STATUS" >&2; exit 1 ;;
        *) sleep 20 ;;
    esac
done

# ── 5. Rsync results back ────────────────────────────────────────────────────
echo "[5/5] Downloading /app/results from pod …"
mkdir -p "$LOCAL_RESULTS_DIR"
runpodctl pod send "$POD_ID" --src "/app/results/" --dst "$LOCAL_RESULTS_DIR/"

echo
echo "[done] benchmark results in $LOCAL_RESULTS_DIR"
echo "[done] aggregate: uv run python benchmark/aggregate_per_step.py"
