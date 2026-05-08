#!/usr/bin/env bash
set -Eeuo pipefail

die() {
  printf '[package:error] %s\n' "$*" >&2
  exit 1
}

note() {
  printf '[package] %s\n' "$*"
}

usage() {
  cat <<'HELP'
Usage:
  scripts/package.sh [output.zip]
  scripts/package.sh --check
  scripts/package.sh --check-archive path/to/archive.zip

Creates a clean source zip from repo root.

Packaging backend:
  - Uses the zip CLI when available.
  - Falls back to Python 3 standard-library zipfile when zip is unavailable.
  - In --check mode, if no archive backend is available, performs a static
    exclusion-pattern check instead of failing CI with a missing zip binary.
HELP
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"

zip_excludes=(
  ".git/*"
  "*/.git/*"
  ".codeseeq/*"
  "*/.codeseeq/*"
  "__MACOSX/*"
  "*/__MACOSX/*"
  ".DS_Store"
  "*/.DS_Store"
  ".env"
  ".env.*"
  "*.env"
  "*.env.*"
  "prod.env"
  "production.env"
  "dist/*"
  "*/dist/*"
  "build/*"
  "*/build/*"
  "node_modules/*"
  "*/node_modules/*"
  "workspace/*"
  "*/workspace/*"
  "__pycache__/*"
  "*/__pycache__/*"
  "*.pyc"
  "logs/*"
  "*/logs/*"
  "*.log"
  "*.zip"
)

have_zip_cli() {
  command -v zip >/dev/null 2>&1
}

have_unzip_cli() {
  command -v unzip >/dev/null 2>&1
}

have_python3() {
  command -v python3 >/dev/null 2>&1
}

resolve_abs_path() {
  local path="$1"
  local dir base
  dir="$(cd "$(dirname "$path")" && pwd)"
  base="$(basename "$path")"
  printf '%s/%s' "$dir" "$base"
}

create_package_with_zip() {
  local output_abs="$1"
  local output_rel=""
  if [[ "$output_abs" == "$repo_root/"* ]]; then
    output_rel="${output_abs#"$repo_root/"}"
  fi

  local -a cmd=(zip -rq "$output_abs" .)
  local pattern
  for pattern in "${zip_excludes[@]}"; do
    cmd+=(-x "$pattern")
  done
  if [[ -n "$output_rel" ]]; then
    cmd+=(-x "$output_rel")
  fi

  (
    cd "$repo_root"
    "${cmd[@]}"

    # .env.example is intentionally included even though .env.* is excluded.
    if [[ -f .env.example ]]; then
      zip -q "$output_abs" .env.example
    fi
  )
}

create_package_with_python() {
  local output_abs="$1"
  local output_rel=""
  if [[ "$output_abs" == "$repo_root/"* ]]; then
    output_rel="${output_abs#"$repo_root/"}"
  fi

  CODESEEQ_REPO_ROOT="$repo_root" \
  CODESEEQ_OUTPUT_ZIP="$output_abs" \
  CODESEEQ_OUTPUT_REL="$output_rel" \
  python3 - <<'PY'
import fnmatch
import os
import zipfile
from pathlib import Path

repo = Path(os.environ["CODESEEQ_REPO_ROOT"]).resolve()
out = Path(os.environ["CODESEEQ_OUTPUT_ZIP"]).resolve()
out_rel = os.environ.get("CODESEEQ_OUTPUT_REL", "")
patterns = [
    ".git/*",
    "*/.git/*",
    ".codeseeq/*",
    "*/.codeseeq/*",
    "__MACOSX/*",
    "*/__MACOSX/*",
    ".DS_Store",
    "*/.DS_Store",
    ".env",
    ".env.*",
    "*.env",
    "*.env.*",
    "prod.env",
    "production.env",
    "dist/*",
    "*/dist/*",
    "build/*",
    "*/build/*",
    "node_modules/*",
    "*/node_modules/*",
    "workspace/*",
    "*/workspace/*",
    "__pycache__/*",
    "*/__pycache__/*",
    "*.pyc",
    "logs/*",
    "*/logs/*",
    "*.log",
    "*.zip",
]
if out_rel:
    patterns.append(out_rel)


def excluded(rel: str) -> bool:
    if rel == ".env.example":
        return False
    for pat in patterns:
        if fnmatch.fnmatch(rel, pat):
            return True
        # For directory patterns like dist/*, match nested paths too.
        if pat.endswith("/*"):
            root = pat[:-2]
            if rel == root or rel.startswith(root + "/") or fnmatch.fnmatch(rel, root + "/*"):
                return True
    return False

out.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(repo):
        root_path = Path(root)
        rel_root = root_path.relative_to(repo).as_posix()
        if rel_root == ".":
            rel_root = ""

        # Prune excluded directories before walking into them.
        kept_dirs = []
        for d in dirs:
            rel_d = f"{rel_root}/{d}" if rel_root else d
            if excluded(rel_d) or excluded(rel_d + "/dummy"):
                continue
            kept_dirs.append(d)
        dirs[:] = kept_dirs

        for name in files:
            rel = f"{rel_root}/{name}" if rel_root else name
            if excluded(rel):
                continue
            zf.write(root_path / name, rel)
PY
}

create_package() {
  local output_zip="$1"
  mkdir -p "$(dirname "$output_zip")"
  rm -f "$output_zip"

  local output_abs
  output_abs="$(resolve_abs_path "$output_zip")"

  if have_zip_cli; then
    create_package_with_zip "$output_abs"
  elif have_python3; then
    note "zip not found; using Python 3 zipfile fallback" >&2
    create_package_with_python "$output_abs"
  else
    die "cannot create zip: install zip or python3"
  fi

  printf '%s\n' "$output_abs"
}

archive_entries() {
  local archive="$1"

  if have_unzip_cli; then
    unzip -Z -1 "$archive"
  elif have_python3; then
    python3 - "$archive" <<'PY'
import sys
import zipfile
with zipfile.ZipFile(sys.argv[1]) as zf:
    for name in zf.namelist():
        print(name)
PY
  else
    return 1
  fi
}

validate_archive() {
  local archive="$1"
  [[ -f "$archive" ]] || die "archive not found: $archive"

  local tmpdir entries_file
  tmpdir="$(mktemp -d)"
  entries_file="$tmpdir/entries.txt"
  if ! archive_entries "$archive" > "$entries_file"; then
    rm -rf "$tmpdir"
    die "cannot inspect archive: $archive"
  fi

  local failures=0
  local entry normalized base segment_path
  local has_env_example=0
  while IFS= read -r entry; do
    [[ -n "$entry" ]] || continue
    normalized="${entry%/}"
    base="${normalized##*/}"
    segment_path="/${normalized}/"

    if [[ "$base" == ".env.example" ]]; then
      has_env_example=1
      continue
    fi

    case "$segment_path" in
      */.git/*|*/.codeseeq/*|*/dist/*|*/workspace/*|*/__MACOSX/*|*/node_modules/*|*/__pycache__/*|*/logs/*)
        printf '[package:check:error] forbidden path in archive: %s\n' "$entry" >&2
        failures=$((failures + 1))
        continue
        ;;
    esac

    case "$base" in
      .DS_Store|*.pyc|*.zip|*.log)
        printf '[package:check:error] forbidden file in archive: %s\n' "$entry" >&2
        failures=$((failures + 1))
        continue
        ;;
      .env|.env.*|*.env|*.env.*|prod.env|production.env)
        printf '[package:check:error] env-like file in archive: %s\n' "$entry" >&2
        failures=$((failures + 1))
        continue
        ;;
    esac
  done < "$entries_file"

  if (( has_env_example == 0 )); then
    printf '[package:check:error] .env.example is missing from archive\n' >&2
    failures=$((failures + 1))
  fi

  rm -rf "$tmpdir"
  if (( failures > 0 )); then
    die "archive check failed with ${failures} issue(s): $archive"
  fi
}

static_package_check() {
  local failures=0
  local required_patterns=(
    '".env"'
    '".env.*"'
    '"*.env"'
    '"*.env.*"'
    '"prod.env"'
    '"production.env"'
    '".git/*"'
    '"*/.git/*"'
    '".codeseeq/*"'
    '"*/.codeseeq/*"'
    '"__MACOSX/*"'
    '"*/.DS_Store"'
    '"dist/*"'
    '"*/dist/*"'
    '"build/*"'
    '"*/build/*"'
    '"node_modules/*"'
    '"*/node_modules/*"'
    '"workspace/*"'
    '"*/workspace/*"'
    '"__pycache__/*"'
    '"*/__pycache__/*"'
    '"*.pyc"'
    '"logs/*"'
    '"*/logs/*"'
    '"*.log"'
    '"*.zip"'
  )

  local pattern
  for pattern in "${required_patterns[@]}"; do
    if ! grep -Fq "$pattern" "$repo_root/scripts/package.sh"; then
      printf '[package:check:error] missing package exclusion pattern: %s\n' "$pattern" >&2
      failures=$((failures + 1))
    fi
  done

  if [[ ! -f "$repo_root/.env.example" ]]; then
    printf '[package:check:error] .env.example is missing from repository root\n' >&2
    failures=$((failures + 1))
  fi

  if (( failures > 0 )); then
    die "static package check failed with ${failures} issue(s)"
  fi

  note "archive tooling unavailable; static package check passed"
}

package_check() {
  local archive tmpdir

  if ! have_zip_cli && ! have_python3; then
    static_package_check
    return 0
  fi

  if ! have_unzip_cli && ! have_python3; then
    static_package_check
    return 0
  fi

  tmpdir="$(mktemp -d)"
  trap 'rm -rf "${tmpdir:-}"' EXIT
  archive="$tmpdir/codeseeq-package-check.zip"

  create_package "$archive" >/dev/null

  validate_archive "$archive"

  note "package check passed"
  rm -rf "$tmpdir"
  trap - EXIT
}

main() {
  case "${1:-}" in
    -h|--help)
      usage
      ;;
    --check)
      shift
      [[ $# -eq 0 ]] || die "--check does not accept output path"
      package_check
      ;;
    --check-archive)
      shift
      [[ $# -eq 1 ]] || die "--check-archive requires exactly one archive path"
      validate_archive "$1"
      note "archive check passed: $1"
      ;;
    *)
      [[ $# -le 1 ]] || die "too many arguments"
      local timestamp default_zip
      timestamp="$(date +%Y%m%d-%H%M%S)"
      default_zip="${repo_root}/dist/codeseeq-${timestamp}.zip"
      create_package "${1:-$default_zip}"
      ;;
  esac
}

main "$@"
