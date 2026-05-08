#!/usr/bin/env bash

codeseeq_detect_container() {
  if [[ -n "${CONTAINER:-}" ]]; then
    case "$CONTAINER" in
      podman|docker) ;;
      *)
        echo "[codeseeq:error] unsupported container runtime: $CONTAINER. Use CONTAINER=podman or CONTAINER=docker." >&2
        return 1
        ;;
    esac
    if ! command -v "$CONTAINER" >/dev/null 2>&1; then
      echo "[codeseeq:error] container runtime not found: $CONTAINER" >&2
      return 1
    fi
    printf '%s\n' "$CONTAINER"
    return 0
  fi

  if command -v podman >/dev/null 2>&1; then
    printf '%s\n' podman
    return 0
  fi

  if command -v docker >/dev/null 2>&1; then
    echo "[codeseeq:warn] podman not found; using docker as compatible fallback" >&2
    printf '%s\n' docker
    return 0
  fi

  echo "[codeseeq:error] no container runtime found. Install Podman (preferred) or Docker." >&2
  return 1
}
