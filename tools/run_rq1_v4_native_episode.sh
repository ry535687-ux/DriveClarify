#!/usr/bin/env bash
# Exactly one native RQ1-V4 episode.  A crashed CARLA process may be restarted
# only before a world exists and before the evaluator/agent is launched.
set -Eeuo pipefail

repo=/home/buaa/wrh/DriveClarify
simlingo=/home/buaa/wrh/simlingo
python=/home/buaa/anaconda3/envs/simlingo/bin/python
config=$(realpath "${1:?config required}")
route=$(realpath "${2:?route required}")
seed=${3:?seed required}
rpc_port=${4:?rpc port required}
answer=${5:-NONE}
output=$(realpath -m "${6:?output required}")
attempt_kind=${7:-FORMAL_NO_SCIENTIFIC_RETRY}
route_id=$($python -c 'import sys,xml.etree.ElementTree as E; print(E.parse(sys.argv[1]).getroot().find("route").attrib["id"])' "$route")
town=$($python -c 'import sys,xml.etree.ElementTree as E; print(E.parse(sys.argv[1]).getroot().find("route").attrib["town"])' "$route")
[[ ! -e "$output" ]] || exit 67
mkdir -p "$output/process_job"

server_pid= server_pgid= broker_pid=
evaluator_exit=125
server_start_attempts=0
preworld_failures=0
world_observed=0
port_open() { timeout 1 bash -c "</dev/tcp/127.0.0.1/$1" >/dev/null 2>&1; }
group_alive() { [[ -n "$1" ]] && ps -eo pgid=,stat= | awk -v pg="$1" '$1==pg && $2!~/^Z/{f=1} END{exit !f}'; }
stop_group() {
  local pg=$1 deadline
  group_alive "$pg" || return 0
  kill -TERM -- "-$pg" 2>/dev/null || true
  deadline=$(( $(date +%s) + 20 ))
  while group_alive "$pg" && (( $(date +%s) < deadline )); do sleep 0.1; done
  group_alive "$pg" && kill -KILL -- "-$pg" 2>/dev/null || true
}
release_current_server() {
  set +e
  [[ -z "$server_pgid" ]] || stop_group "$server_pgid"
  [[ -z "$server_pid" ]] || wait "$server_pid" 2>/dev/null || true
  server_pid=
  server_pgid=
  for _ in $(seq 1 160); do
    if ! port_open "$rpc_port" && ! port_open "$((rpc_port+1))" && ! port_open "$((rpc_port+102))"; then break; fi
    sleep 0.2
  done
  set -e
}
cleanup() {
  local wrapper_status=$? ports=0 residue=""
  set +e
  if [[ -n "$broker_pid" ]] && kill -0 "$broker_pid" 2>/dev/null; then
    kill -TERM "$broker_pid" 2>/dev/null || true
    wait "$broker_pid" 2>/dev/null || true
  fi
  [[ -z "$server_pgid" ]] || stop_group "$server_pgid"
  [[ -z "$server_pid" ]] || wait "$server_pid" 2>/dev/null || true
  for _ in $(seq 1 160); do
    if ! port_open "$rpc_port" && ! port_open "$((rpc_port+1))" && ! port_open "$((rpc_port+102))"; then ports=1; break; fi
    sleep 0.2
  done
  [[ -z "$server_pgid" ]] || residue=$(ps -eo pid=,pgid=,stat=,args= | awk -v pg="$server_pgid" '$2==pg && $3!~/^Z/{print}' || true)
  "$python" - "$output/process_job/PROCESS_RECEIPT.json" "$wrapper_status" "$evaluator_exit" "$ports" "$residue" "$attempt_kind" "$server_start_attempts" "$preworld_failures" "$world_observed" <<'PY'
import json,pathlib,sys,time
p=pathlib.Path(sys.argv[1]); kind=sys.argv[6]; starts=int(sys.argv[7]); failures=int(sys.argv[8])
value={
 "schema":"driveclarify.rq1_v4.native-process.v1",
 "wrapper_exit":int(sys.argv[2]),"evaluator_exit":int(sys.argv[3]),
 "ports_released":bool(int(sys.argv[4])),"process_residue":bool(sys.argv[5]),
 "cleanup_pass":bool(int(sys.argv[4])) and not bool(sys.argv[5]),
 "finished_epoch":int(time.time()),"attempt_kind":kind,
 "scientific_retry":False,"formal_seed_replacement":False,
 "server_start_attempts":starts,"preworld_startup_failures":failures,
 "infrastructure_retries":failures,"world_observed_before_evaluator":bool(int(sys.argv[9])),
 "startup_retry_boundary":"PRE_WORLD_PRE_EVALUATOR_ONLY",
}
t=p.with_suffix(p.suffix+".tmp");t.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n");t.replace(p)
PY
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

export CARLA_ROOT=/home/buaa/CARLA_0.9.15
export LEADERBOARD_ROOT=$simlingo/leaderboard_autopilot
export SCENARIO_RUNNER_ROOT=$simlingo/scenario_runner_autopilot
export PYTHONPATH=$repo:$simlingo:$CARLA_ROOT/PythonAPI/carla:$LEADERBOARD_ROOT:$SCENARIO_RUNNER_ROOT:$simlingo/team_code
export DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority XDG_SESSION_TYPE=x11 XDG_SESSION_REMOTE=false
export __NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia SDL_VIDEODRIVER=x11
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
export DATAGEN=0 SAVE_TF_LABELS=0 TMP_VISU=0 DEBUG_CHALLENGE=0 TOWN=$town REPETITION=0
export DRIVECLARIFY_RANDOM_BACKGROUND_VEHICLE_COUNT=0 RANDOM_BACKGROUND_VEHICLE_COUNT=0
export DRIVECLARIFY_TRAFFIC_MANAGER_RANDOM_GENERATION=0
export ROUTES=$route SAVE_PATH=$output/official_data/
export DRIVECLARIFY_V11_CONFIG=$config DRIVECLARIFY_V11_CONFIG_SHA256=$(sha256sum "$config" | awk '{print $1}')
export DRIVECLARIFY_V11_OWNER_DIR=$output/owner_evidence
export DRIVECLARIFY_PROBE_ENABLED=0 DRIVECLARIFY_SHADOW_V0=0 DRIVECLARIFY_PAPER_MVP_STAGE6A_LIVE=0
unset RECORD_PATH HISTOGRAM TP_STATS RESUME

xrandr --display "$DISPLAY" --query >"$output/process_job/xrandr.txt" 2>"$output/process_job/xrandr.err"
awk '/ connected/{found=1} END{exit !found}' "$output/process_job/xrandr.txt"
! pgrep -x Xvfb >/dev/null && ! pgrep -x Xvnc >/dev/null && ! pgrep -x Xtigervnc >/dev/null
for port in "$rpc_port" "$((rpc_port+1))" "$((rpc_port+102))"; do ! port_open "$port" || exit 66; done

# V3's two preserved ORD-CRITICAL failures died here with UE4
# "close: Bad file descriptor" before a CARLA world or evaluator existed.
# Retry the server launch at this pre-world boundary, using the same cell and
# seed.  Once a world exists the loop is permanently closed for this episode.
for start_attempt in 1 2 3; do
  server_start_attempts=$start_attempt
  server_log="$output/process_job/carla_server_start_$(printf '%02d' "$start_attempt").log"
  setsid "$repo/reports/driveclarify_v10_clear_passthrough_safe_replan_method_development/startup_diagnostics/carla_epic_quality_launcher_v10.sh" CarlaUE4 -nosound "-carla-rpc-port=$rpc_port" "-carla-streaming-port=$((rpc_port+1))" -quality-level=Epic -fps=20 >"$server_log" 2>&1 &
  server_pid=$!
  ready_listener=0
  for _ in $(seq 1 960); do
    read -r server_pgid _ < <(ps -o pgid=,sid= -p "$server_pid" 2>/dev/null || true)
    if ! kill -0 "$server_pid" 2>/dev/null; then break; fi
    if port_open "$rpc_port"; then ready_listener=1; break; fi
    sleep 0.25
  done
  if [[ "$ready_listener" != 1 ]]; then
    preworld_failures=$((preworld_failures+1))
    release_current_server
    continue
  fi

  world_ready=0
  for _ in $(seq 1 60); do
    if timeout 8 "$python" - "$rpc_port" "$output/process_job/world_ready.json" "$start_attempt" <<'PY'
import carla,json,pathlib,sys,time
c=carla.Client("127.0.0.1",int(sys.argv[1])); c.set_timeout(5.0)
w=c.get_world(); value={"map":w.get_map().name,"frame":w.get_snapshot().frame,"ready_epoch":int(time.time()),"server_start_attempt":int(sys.argv[3])}
pathlib.Path(sys.argv[2]).write_text(json.dumps(value,sort_keys=True)+"\n")
PY
    then world_ready=1; world_observed=1; break; fi
    if ! kill -0 "$server_pid" 2>/dev/null; then break; fi
    sleep 2
  done
  if [[ "$world_ready" == 1 ]]; then
    cp "$server_log" "$output/process_job/carla_server.log"
    break
  fi
  preworld_failures=$((preworld_failures+1))
  release_current_server
done
[[ "$world_observed" == 1 ]] || exit 68
sleep 3

if [[ "$answer" != NONE ]]; then
  "$python" -u "$repo/tools/rq1_v2_answer_broker.py" --exchange "$output/owner_evidence/oracle_exchange" --selected-candidate-id "$answer" >"$output/process_job/answer_broker.log" 2>&1 &
  broker_pid=$!
fi

set +e
(cd "$simlingo" && timeout --signal=TERM --kill-after=30 420 \
  "$python" "$LEADERBOARD_ROOT/leaderboard/leaderboard_evaluator.py" \
  --host=127.0.0.1 --port="$rpc_port" --traffic-manager-port="$((rpc_port+102))" \
  --traffic-manager-seed="$seed" --routes="$route" --routes-subset="$route_id" \
  --repetitions=1 --track=SENSORS --checkpoint="$output/official_checkpoint.json" \
  --debug-checkpoint="$output/official_debug.txt" \
  --agent="$repo/driveclarify_rq1_v2/simlingo_agent.py" \
  --agent-config="$config" --debug=0 --timeout=300) >"$output/process_job/evaluator.log" 2>&1
evaluator_exit=$?
set -e
exit "$evaluator_exit"
