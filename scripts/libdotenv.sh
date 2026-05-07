#!/usr/bin/env bash
# shellcheck shell=bash

codeseeq_dotenv_key_allowed() {
  case "$1" in
    DEEPSEEK_API_KEY|HOST_UID|HOST_GID|CODESEEQ_*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

codeseeq_trim_leading_ws() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  printf '%s' "$value"
}

codeseeq_trim_trailing_ws() {
  local value="$1"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

codeseeq_decode_env_value() {
  local value="$1"

  if [[ "$value" == \"*\" && "$value" == *\" && "${#value}" -ge 2 ]]; then
    value="${value:1:${#value}-2}"
    value="${value//\\\"/\"}"
    value="${value//\\\\/\\}"
    printf '%s' "$value"
    return 0
  fi

  if [[ "$value" == \'*\' && "$value" == *\' && "${#value}" -ge 2 ]]; then
    value="${value:1:${#value}-2}"
    printf '%s' "$value"
    return 0
  fi

  printf '%s' "$value"
}

codeseeq_load_dotenv() {
  local env_file="${1:-.env}"
  [[ -f "$env_file" ]] || return 0

  local line trimmed key raw value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    trimmed="$(codeseeq_trim_leading_ws "$line")"
    [[ -z "$trimmed" ]] && continue
    [[ "$trimmed" == \#* ]] && continue

    if [[ "$trimmed" == export[[:space:]]* ]]; then
      trimmed="${trimmed#export}"
      trimmed="$(codeseeq_trim_leading_ws "$trimmed")"
    fi

    if [[ "$trimmed" =~ ^([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      raw="${BASH_REMATCH[2]}"
      raw="$(codeseeq_trim_leading_ws "$raw")"
      if [[ "$raw" != \"* && "$raw" != \'* ]]; then
        raw="${raw%%#*}"
        raw="$(codeseeq_trim_trailing_ws "$raw")"
      fi
      value="$(codeseeq_decode_env_value "$raw")"

      if codeseeq_dotenv_key_allowed "$key" && [[ -z "${!key+x}" ]]; then
        printf -v "$key" '%s' "$value"
        export "$key"
      fi
    fi
  done < "$env_file"
}
