#!/usr/bin/env bash
# GitHub-hosted disposable Linux only: aggregate cgroup limits and owned scope cleanup.
set -Eeuo pipefail
test "${GITHUB_ACTIONS:-}" = true
gate=$1
python=$(command -v python)
work=$(mktemp -d "$RUNNER_TEMP/w23-private-XXXXXXXX")
evidence="$RUNNER_TEMP/w23-evidence"
mkdir -p "$evidence"
ulimit -c 0
ulimit -f 1048576
if [ "$gate" = pytest ]; then
  command=("$python" -B -m pytest -q --tb=short --basetemp "$work/pytest" --junitxml="$evidence/pytest.xml")
elif [ "$gate" = lifecycle ]; then
  command=("$python" -B -m pytest -q --basetemp "$work/pytest"
    'tests/unit/skills/test_shell.py::test_posix_shell_process_terminates_parent_and_descendant[timeout-timed_out]'
    'tests/unit/skills/test_shell.py::test_posix_shell_process_terminates_parent_and_descendant[cancel-cancelled]'
    'tests/unit/skills/test_shell.py::test_posix_shell_parent_exit_terminates_descendant_with_inherited_pipes')
elif [ "$gate" = installed ]; then
  command=("$python" -B scripts/verify_installed_package.py --summary-json "$evidence/installed-wheel.json")
else
  echo 'unknown acceptance gate' >&2; exit 2
fi
# A transient scope contains only this acceptance and descendants. runuser retains
# the non-root test identity; systemd removes remaining members when the scope ends.
unit="w23-${gate}-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
cleanup() {
  code=$?
  trap - EXIT
  if systemctl is-active --quiet "$unit.scope"; then
    sudo systemctl stop "$unit.scope" || exit 125
  fi
  exit "$code"
}
trap cleanup EXIT
set +e
sudo systemd-run --scope --quiet --unit="$unit" \
  -p MemoryMax=4G -p TasksMax=128 -p RuntimeMaxSec=1800 -p KillMode=control-group \
  runuser -u "$(id -un)" -- env PATH="$PATH" HOME="$HOME" \
  TEMP="$work" TMP="$work" TMPDIR="$work" PYTHONDONTWRITEBYTECODE=1 \
  GIT_OPTIONAL_LOCKS=0 AGENT_RUNTIME_DIR="$work/runtime" \
  bash -c 'set -eu; ulimit -c 0; ulimit -f 1048576; uname -a; findmnt -T "$TMPDIR" -o SOURCE,FSTYPE,TARGET;
    group=$(cut -d: -f3 /proc/self/cgroup); cat /proc/self/cgroup;
    printf "effective_memory_max="; cat "/sys/fs/cgroup$group/memory.max";
    printf "effective_pids_max="; cat "/sys/fs/cgroup$group/pids.max";
    exec timeout --signal=TERM --kill-after=10s 1800s "$@"' bounded "${command[@]}" > "$evidence/$gate.log" 2>&1
result=$?
set -e
printf '{"return_code":%s,"memory_limit_bytes":4294967296,"process_limit":128,"timeout_seconds":1800}\n' "$result" > "$evidence/$gate.json"
du -sb "$work" > "$evidence/$gate-disk.txt"
tail -100 "$evidence/$gate.log"
exit "$result"
