#!/usr/bin/env bash
# Infrastructure-repaired launcher for the authorized RQ3-V3 P0 resume only.
set -Eeuo pipefail

repo=/home/buaa/wrh/DriveClarify
simlingo=/home/buaa/wrh/simlingo
python=/home/buaa/anaconda3/envs/simlingo/bin/python
config=$(realpath "${1:?config required}")
route=$(realpath "${2:?route required}")
seed=${3:?engineering seed required}
rpc_port=${4:?rpc port required}
tm_port=${5:?traffic-manager port required}
output=$(realpath -m "${6:?output required}")
runtime_mode=NATIVE_DEFAULT
streaming_port=$((rpc_port+1))
route_id=$($python -c 'import sys,xml.etree.ElementTree as E; print(E.parse(sys.argv[1]).getroot().find("route").attrib["id"])' "$route")
town=$($python -c 'import sys,xml.etree.ElementTree as E; print(E.parse(sys.argv[1]).getroot().find("route").attrib["town"])' "$route")
read -r ephemeral_low ephemeral_high </proc/sys/net/ipv4/ip_local_port_range
for port in "$rpc_port" "$streaming_port" "$tm_port"; do
  (( port < ephemeral_low || port > ephemeral_high )) || exit 69
done
[[ "$rpc_port" != "$streaming_port" && "$rpc_port" != "$tm_port" && "$streaming_port" != "$tm_port" ]] || exit 70
[[ ! -e "$output" ]] || exit 67
mkdir -p "$output/process_job"

server_pid= server_pgid= evaluator_pid= evaluator_pgid=
evaluator_exit=125
server_start_attempts=0
preworld_failures=0
world_observed=0
started_epoch=$(date +%s)
world_ready_epoch=0
evaluator_started_epoch=0
evaluator_finished_epoch=0
prelaunch_bind_pass=0
postworld_tm_bind_pass=0
port_open() { timeout 1 bash -c "</dev/tcp/127.0.0.1/$1" >/dev/null 2>&1; }
bindable() {
  "$python" -c 'import socket,sys; s=socket.socket(); s.bind(("0.0.0.0",int(sys.argv[1]))); s.close()' "$1" >/dev/null 2>&1
}
group_alive() { [[ -n "$1" ]] && ps -eo pgid=,stat= | awk -v pg="$1" '$1==pg && $2!~/^Z/{f=1} END{exit !f}'; }
stop_group() {
  local pg=$1 deadline
  group_alive "$pg" || return 0
  kill -TERM -- "-$pg" 2>/dev/null || true
  deadline=$(( $(date +%s) + 20 ))
  while group_alive "$pg" && (( $(date +%s) < deadline )); do sleep 0.1; done
  group_alive "$pg" && kill -KILL -- "-$pg" 2>/dev/null || true
}
all_ports_listener_free() {
  ! port_open "$rpc_port" && ! port_open "$streaming_port" && ! port_open "$tm_port"
}
release_current_server() {
  set +e
  [[ -z "$server_pgid" ]] || stop_group "$server_pgid"
  [[ -z "$server_pid" ]] || wait "$server_pid" 2>/dev/null || true
  server_pid= server_pgid=
  for _ in $(seq 1 160); do
    if all_ports_listener_free; then return 0; fi
    sleep 0.2
  done
  return 1
}
cleanup() {
  local wrapper_status=$? ports=0 residue="" residue_flag=0
  set +e
  [[ -z "$evaluator_pgid" ]] || stop_group "$evaluator_pgid"
  [[ -z "$server_pgid" ]] || stop_group "$server_pgid"
  [[ -z "$server_pid" ]] || wait "$server_pid" 2>/dev/null || true
  for _ in $(seq 1 160); do
    if all_ports_listener_free; then ports=1; break; fi
    sleep 0.2
  done
  [[ -z "$server_pgid" ]] || residue=$(ps -eo pid=,pgid=,stat=,args= | awk -v pg="$server_pgid" '$2==pg && $3!~/^Z/{print}' || true)
  [[ -z "$residue" ]] || residue_flag=1
  "$python" -c 'import json,pathlib,sys,time; p=pathlib.Path(sys.argv[1]); v={"schema":"driveclarify.rq3-v3-p0.recovery-port-receipt.v1","rpc_port":int(sys.argv[2]),"streaming_port":int(sys.argv[3]),"traffic_manager_port":int(sys.argv[4]),"ephemeral_range":[int(sys.argv[5]),int(sys.argv[6])],"all_ports_outside_ephemeral_range":True,"prelaunch_bind_pass":bool(int(sys.argv[7])),"postworld_tm_bind_pass":bool(int(sys.argv[8])),"listeners_released_after_cleanup":bool(int(sys.argv[9])),"recorded_epoch":int(time.time())}; p.write_text(json.dumps(v,indent=2,sort_keys=True)+"\n")' "$output/process_job/INFRASTRUCTURE_PORT_RECEIPT.json" "$rpc_port" "$streaming_port" "$tm_port" "$ephemeral_low" "$ephemeral_high" "$prelaunch_bind_pass" "$postworld_tm_bind_pass" "$ports"
  "$python" "$repo/tools/run_rq3_v3_p0_scene_qualification.py" finalize-run \
    --output "$output" --wrapper-exit "$wrapper_status" --evaluator-exit "$evaluator_exit" \
    --ports-released "$ports" --process-residue "$residue_flag" --world-observed "$world_observed" \
    --server-start-attempts "$server_start_attempts" --preworld-failures "$preworld_failures" \
    --started-epoch "$started_epoch" --world-ready-epoch "$world_ready_epoch" \
    --evaluator-started-epoch "$evaluator_started_epoch" --evaluator-finished-epoch "$evaluator_finished_epoch" \
    --runtime-mode "$runtime_mode" >"$output/process_job/finalize_run.log" 2>&1 || true
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
export DRIVECLARIFY_ENGINEERING_RUNTIME_MODE=$runtime_mode DRIVECLARIFY_ENGINEERING_SEED=$seed
unset RECORD_PATH HISTOGRAM TP_STATS RESUME CUBLAS_WORKSPACE_CONFIG

xrandr --display "$DISPLAY" --query >"$output/process_job/xrandr.txt" 2>"$output/process_job/xrandr.err"
awk '/ connected/{found=1} END{exit !found}' "$output/process_job/xrandr.txt"
! pgrep -x Xvfb >/dev/null && ! pgrep -x Xvnc >/dev/null && ! pgrep -x Xtigervnc >/dev/null
all_ports_listener_free
bindable "$rpc_port" && bindable "$streaming_port" && bindable "$tm_port"
prelaunch_bind_pass=1

for start_attempt in 1 2 3; do
  server_start_attempts=$start_attempt
  server_log="$output/process_job/carla_server_start_$(printf '%02d' "$start_attempt").log"
  setsid "$repo/reports/driveclarify_v10_clear_passthrough_safe_replan_method_development/startup_diagnostics/carla_epic_quality_launcher_v10.sh" CarlaUE4 -nosound "-carla-rpc-port=$rpc_port" "-carla-streaming-port=$streaming_port" -quality-level=Epic -fps=20 >"$server_log" 2>&1 &
  server_pid=$!
  ready_listener=0
  for _ in $(seq 1 960); do
    read -r server_pgid _ < <(ps -o pgid=,sid= -p "$server_pid" 2>/dev/null || true)
    if ! kill -0 "$server_pid" 2>/dev/null; then break; fi
    if port_open "$rpc_port"; then ready_listener=1; break; fi
    sleep 0.25
  done
  if [[ "$ready_listener" != 1 ]]; then
    preworld_failures=$((preworld_failures+1)); release_current_server; continue
  fi
  world_ready=0
  for _ in $(seq 1 60); do
    if timeout 8 "$python" -c 'import carla,json,pathlib,sys,time; c=carla.Client("127.0.0.1",int(sys.argv[1])); c.set_timeout(5.0); w=c.get_world(); pathlib.Path(sys.argv[2]).write_text(json.dumps({"map":w.get_map().name,"frame":w.get_snapshot().frame,"ready_epoch":int(time.time()),"server_start_attempt":int(sys.argv[3])},sort_keys=True)+"\n")' "$rpc_port" "$output/process_job/world_ready.json" "$start_attempt"
    then world_ready=1; world_observed=1; world_ready_epoch=$(date +%s); break; fi
    if ! kill -0 "$server_pid" 2>/dev/null; then break; fi
    sleep 2
  done
  if [[ "$world_ready" == 1 ]]; then cp "$server_log" "$output/process_job/carla_server.log"; break; fi
  preworld_failures=$((preworld_failures+1)); release_current_server
done
[[ "$world_observed" == 1 ]] || exit 68
sleep 3
! port_open "$tm_port"
bindable "$tm_port"
postworld_tm_bind_pass=1

evaluator_started_epoch=$(date +%s)
set +e
setsid timeout --signal=TERM --kill-after=30 3600 \
  "$python" "$LEADERBOARD_ROOT/leaderboard/leaderboard_evaluator.py" \
  --host=127.0.0.1 --port="$rpc_port" --traffic-manager-port="$tm_port" \
  --traffic-manager-seed="$seed" --routes="$route" --routes-subset="$route_id" \
  --repetitions=1 --track=SENSORS --checkpoint="$output/official_checkpoint.json" \
  --debug-checkpoint="$output/official_debug.txt" \
  --agent="$repo/driveclarify_rq3_native_qualification/simlingo_agent.py" \
  --agent-config="$config" --debug=0 --timeout=480 >"$output/process_job/evaluator.log" 2>&1 &
evaluator_pid=$!
read -r evaluator_pgid _ < <(ps -o pgid=,sid= -p "$evaluator_pid")
wait "$evaluator_pid"
evaluator_exit=$?
set -e
evaluator_finished_epoch=$(date +%s)
exit "$evaluator_exit"
