#!/usr/bin/env python3
"""Read-only local :1 clear-task panel and fixed first-observed-event captures.

No CARLA/model/controller imports, window moves, screenshots of the root desktop,
process signals, world ticks or simulator writes. Capture timing is wall-clock
sampling after a log event is observed; pixel/log frame equality is not proven.
"""
import argparse
import ctypes as C
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

LABELS = ('RESEARCH DEBUG VIEW / 研究调试视图',
          'NO FORMAL SAFETY GUARANTEE / 无正式安全保证',
          'SIMULATION ONLY / 仅仿真')
EVENT_CATEGORIES = ('FIRST_CONTROL', 'TASK_UPDATE', 'FIRST_COLLISION')


def now():
    return datetime.datetime.now().astimezone().isoformat()


def dump(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        return {'_read_error': type(exc).__name__ + ': ' + str(exc)}


def read_log(path, budget=2*1024*1024):
    """Bounded head+tail reads preserve first triggers and current log samples."""
    path = Path(path)
    evidence = {'path': str(path), 'read_started_local': now(), 'read_mode': 'BOUNDED_HEAD_AND_TAIL'}
    try:
        with path.open('rb') as stream:
            stat = os.fstat(stream.fileno())
            if stat.st_size <= budget:
                chunks = [(0, stream.read())]
            else:
                head_bytes = min(256*1024, budget//2)
                chunks = [(0, stream.read(head_bytes))]
                offset = max(head_bytes, stat.st_size-(budget-head_bytes))
                stream.seek(offset)
                chunks.append((offset, stream.read(budget-head_bytes)))
        rows, seen, errors = [], set(), 0
        for offset, raw in chunks:
            pieces = raw.splitlines(keepends=True)
            if offset and pieces:
                pieces = pieces[1:]  # first line may begin mid-record; never assume its boundary
            for line in pieces:
                if not line.endswith(b'\n'):
                    continue  # writer may not have completed this record yet
                try:
                    row = json.loads(line)
                    identity = hashlib.sha256(line).hexdigest()
                    if isinstance(row, dict) and identity not in seen:
                        rows.append(row); seen.add(identity)
                except ValueError:
                    errors += 1
        evidence.update(size_at_read=stat.st_size, mtime_ns=stat.st_mtime_ns,
                        chunks=[{'offset': offset, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                                for offset, raw in chunks], malformed_complete_lines=errors)
        return rows, evidence
    except OSError as exc:
        evidence['read_error'] = type(exc).__name__ + ': ' + str(exc)
        return [], evidence


def first_matching(rows, predicate):
    return next((row for row in rows if predicate(row)), None)


def current_snapshot(report_root):
    root = Path(report_root).resolve()
    state = read_json(root/'STATE.json')
    current = state.get('current_run')
    if isinstance(current, dict):
        current = current.get('run_id')
    manifest = read_json(root/'DEV_MANIFEST.json')
    matches = [row for row in manifest.get('runs', []) if row.get('run_id') == current]
    snapshot = {'sampled_local': now(), 'state': state, 'current_run': current,
                'manifest_run': matches[0] if len(matches) == 1 else None,
                'read_only': True, 'labels': LABELS, 'logs': {}, 'log_read_evidence': {}}
    if len(matches) != 1:
        snapshot['scope_error'] = 'NO_UNIQUE_CURRENT_RUN_IN_DEV_MANIFEST'
        snapshot['triggers'] = {}
        return snapshot
    run = matches[0]; native = Path(run['output']); owner = native/'owner_evidence'
    snapshot['config'] = read_json(run['config_path'])
    files = {'task_events': owner/'CLEAR_TASK_EVENTS.jsonl',
             'control_plan': owner/'CLEAR_TASK_CONTROL_PLAN.jsonl',
             'model_input': owner/'CLEAR_TASK_MODEL_INPUT.jsonl',
             'world_actors': native/'diagnostic_observer/WORLD_ACTOR_TIMELINE.jsonl',
             'collisions': native/'diagnostic_observer/COLLISION_SOURCE_TIMELINE.jsonl',
             'observer_summary': native/'diagnostic_observer/OBSERVER_SUMMARY.jsonl'}
    rows = {}
    for key, path in files.items():
        values, evidence = read_log(path)
        rows[key] = values
        snapshot['logs'][key] = values[-1] if values else None
        snapshot['log_read_evidence'][key] = evidence
    world = snapshot['logs'].get('world_actors') or {}
    ego_id = world.get('ego_actor_id')
    first_control = first_matching(rows['control_plan'], lambda r:
                                   isinstance(r.get('control_return_count'), (int, float)) and r['control_return_count'] > 0)
    task_received = first_matching(rows['task_events'], lambda r: r.get('event') == 'CLEAR_TASK_RECEIVED')
    first_collision = first_matching(rows['collisions'], lambda r:
        ego_id is not None and r.get('ego_actor_id') == ego_id and r.get('event') == 'ORIGINAL_COLLISION_CALLBACK_RESULT')
    snapshot['triggers'] = {key: value for key, value in
                            zip(EVENT_CATEGORIES, (first_control, task_received, first_collision)) if value is not None}
    snapshot['first_collision_scope'] = 'FIRST_OBSERVED_RAW_CALLBACK_FOR_LOGGED_EGO_NOT_AUTOMATICALLY_ACCEPTED_SAFETY_EVENT'
    snapshot['ego_actor_id_basis'] = 'LATEST_WORLD_ACTOR_OBSERVATION' if ego_id is not None else 'UNKNOWN_NO_EGO_ID_NO_COLLISION_TRIGGER'
    snapshot['latest_task_received'] = next((r for r in reversed(rows['task_events']) if r.get('event')=='CLEAR_TASK_RECEIVED'), None)
    snapshot['latest_route_binding'] = next((r for r in reversed(rows['task_events']) if r.get('event') in
                                           ('TASK_ROUTE_BINDING_PROPOSED','CLEAR_TASK_ROUTE_INSTALLED')), None)
    snapshot['native_process_receipt'] = read_json(native/'process_job/PROCESS_RECEIPT.json')
    return snapshot


def parse_window_tree(text):
    windows = []
    for line in text.splitlines():
        match = re.match(r'^\s*(0x[0-9a-fA-F]+)\s+"([^"]*)"', line)
        if match and match.group(2) == 'CarlaUE4':
            windows.append(int(match.group(1),16))
    return windows


def parse_window_info(text):
    fields = {}
    for name, pattern in [('width',r'Width:\s*(\d+)'),('height',r'Height:\s*(\d+)'),
                          ('x',r'Absolute upper-left X:\s*(-?\d+)'),('y',r'Absolute upper-left Y:\s*(-?\d+)')]:
        match = re.search(pattern, text)
        fields[name] = int(match.group(1)) if match else None
    fields['viewable'] = 'Map State: IsViewable' in text
    return fields


def process_identity(pid, proc_root=Path('/proc')):
    base = Path(proc_root)/str(pid)
    tail = (base/'stat').read_text().rsplit(')',1)[1].split()
    command = [p.decode('utf-8','replace') for p in (base/'cmdline').read_bytes().split(b'\0') if p]
    try:
        environment = dict(p.decode('utf-8','replace').split('=',1)
                           for p in (base/'environ').read_bytes().split(b'\0') if b'=' in p)
    except OSError:
        environment = {}
    return {'pid': int(pid), 'ppid': int(tail[1]), 'pgid': int(tail[2]),
            'starttime_ticks': int(tail[19]), 'uid': base.stat().st_uid,
            'argv': command, 'owner_directory_env': environment.get('DRIVECLARIFY_V11_OWNER_DIR')}


def process_belongs_to_run(pid, run, rpc_port=28100, proc_root=Path('/proc')):
    identity = process_identity(pid,proc_root)
    argv = identity['argv']
    executable = Path(argv[0]).name if argv else ''
    native = executable.startswith('CarlaUE4') and 'Shipping' in executable
    port = ('-carla-rpc-port='+str(rpc_port)) in argv or any(
        value=='-carla-rpc-port' and index+1<len(argv) and argv[index+1]==str(rpc_port)
        for index,value in enumerate(argv))
    output = str(Path(run['output']).resolve())
    owner = str(Path(output)/'owner_evidence')
    env_bound = identity['owner_directory_env'] == owner
    ancestry, matched_ancestor, visited = [], None, set()
    parent = identity['ppid']
    for _ in range(12):
        if parent <= 1 or parent in visited:
            break
        visited.add(parent)
        try:
            value = process_identity(parent,proc_root)
        except (OSError,ValueError,IndexError):
            break
        ancestry.append({'pid':value['pid'],'ppid':value['ppid'],'pgid':value['pgid'],'starttime_ticks':value['starttime_ticks']})
        if output in value['argv']:
            matched_ancestor = value['pid']
            break
        parent = value['ppid']
    valid = native and port and identity['uid']==os.getuid() and (env_bound or matched_ancestor is not None)
    return {'valid':valid,'native_identity':identity,'rpc_port':rpc_port,
            'native_shipping_binary':native,'rpc_argument_matches':port,
            'current_run_env_binding':env_bound,'current_run_launcher_ancestor_pid':matched_ancestor,
            'ancestry':ancestry,'process_group_basis':'NATIVE_PGID_PLUS_EXACT_RUN_OWNER_ENV_OR_LAUNCHER_ANCESTRY',
            'reason':None if valid else 'WINDOW_PID_NOT_VERIFIED_AS_CURRENT_RUN_NATIVE_CARLA'}


def command(argv):
    return subprocess.check_output(argv,text=True,stderr=subprocess.STDOUT,timeout=5)


def discover_native_window(run, rpc_port=28100):
    if os.environ.get('DISPLAY') != ':1':
        raise RuntimeError('Native capture requires local DISPLAY=:1')
    tree = command(['xwininfo','-display',':1','-root','-tree'])
    eligible, rejected = [], []
    for window_id in parse_window_tree(tree):
        try:
            properties = command(['xprop','-display',':1','-id',hex(window_id),'_NET_WM_PID','WM_NAME'])
            match = re.search(r'_NET_WM_PID\([^)]*\)\s*=\s*(\d+)', properties)
            if not match:
                raise RuntimeError('No _NET_WM_PID')
            ownership = process_belongs_to_run(int(match.group(1)),run,rpc_port)
            info_text = command(['xwininfo','-display',':1','-id',hex(window_id)])
            geometry = parse_window_info(info_text)
            record={'window_id':hex(window_id),'title':'CarlaUE4','ownership':ownership,
                    'geometry':geometry,'xprop':properties,'xwininfo':info_text}
            if ownership['valid'] and geometry['viewable'] and geometry['width'] and geometry['height']:
                eligible.append(record)
            else:
                rejected.append(record)
        except Exception as exc:
            rejected.append({'window_id':hex(window_id),'error':type(exc).__name__+': '+str(exc)})
    if len(eligible) != 1:
        raise RuntimeError('Expected exactly one visible current-run native window; eligible=%s rejected=%s' %
                           (len(eligible), json.dumps(rejected,ensure_ascii=False)))
    return eligible[0]


class XImage(C.Structure):
    _fields_=[('width',C.c_int),('height',C.c_int),('xoffset',C.c_int),('format',C.c_int),
              ('data',C.c_void_p),('byte_order',C.c_int),('bitmap_unit',C.c_int),('bitmap_bit_order',C.c_int),
              ('bitmap_pad',C.c_int),('depth',C.c_int),('bytes_per_line',C.c_int),('bits_per_pixel',C.c_int),
              ('red_mask',C.c_ulong),('green_mask',C.c_ulong),('blue_mask',C.c_ulong)]


def capture_x11_window(window, destination):
    """XGetImage of one verified client drawable; never use a desktop screenshot."""
    if os.environ.get('DISPLAY') != ':1':
        raise RuntimeError('Capture restricted to DISPLAY=:1')
    from PIL import Image
    if Path(destination).exists():
        raise FileExistsError(destination)
    x=C.CDLL('libX11.so.6')
    x.XOpenDisplay.argtypes=[C.c_char_p];x.XOpenDisplay.restype=C.c_void_p
    x.XGetImage.argtypes=[C.c_void_p,C.c_ulong,C.c_int,C.c_int,C.c_uint,C.c_uint,C.c_ulong,C.c_int]
    x.XGetImage.restype=C.POINTER(XImage)
    x.XDestroyImage.argtypes=[C.POINTER(XImage)]
    x.XCloseDisplay.argtypes=[C.c_void_p]
    x.XSync.argtypes=[C.c_void_p,C.c_int]
    error_type=C.CFUNCTYPE(C.c_int,C.c_void_p,C.c_void_p)
    errors=[]
    @error_type
    def on_error(display,event):
        errors.append('X11_ERROR_WINDOW_MAY_HAVE_EXITED');return 0
    x.XSetErrorHandler.argtypes=[C.c_void_p];x.XSetErrorHandler.restype=C.c_void_p
    old_handler=x.XSetErrorHandler(C.cast(on_error,C.c_void_p))
    display=x.XOpenDisplay(b':1')
    if not display:
        x.XSetErrorHandler(old_handler)
        raise RuntimeError('Cannot open local X11 display :1')
    image=None
    try:
        width,height=window['geometry']['width'],window['geometry']['height']
        if not (0<width<=7680 and 0<height<=4320):
            raise ValueError('Unexpected native window dimensions')
        image=x.XGetImage(display,int(window['window_id'],16),0,0,width,height,C.c_ulong(-1).value,2)
        x.XSync(display,0)
        if not image or errors:
            raise RuntimeError('XGetImage failed: '+str(errors))
        value=image.contents
        if (value.bits_per_pixel!=32 or value.byte_order!=0 or value.red_mask!=0xFF0000 or
                value.green_mask!=0xFF00 or value.blue_mask!=0xFF):
            raise RuntimeError('Unsupported XImage pixel layout; no conversion guessed')
        raw=C.string_at(value.data,value.bytes_per_line*value.height)
        Image.frombytes('RGB',(value.width,value.height),raw,'raw','BGRX',value.bytes_per_line,1).save(destination)
    finally:
        if image:x.XDestroyImage(image)
        x.XCloseDisplay(display)
        x.XSetErrorHandler(old_handler)


def capture_event(root, output, snapshot, category, discover=discover_native_window,
                  capture=capture_x11_window, load_snapshot=current_snapshot):
    run_id=snapshot['current_run'];directory=Path(output)/run_id/category
    if directory.exists():
        return {'status':'PREVIOUS_ATTEMPT_PRESERVED_NO_RETRY','category':category,'directory':str(directory)}
    directory.mkdir(parents=True,exist_ok=False)
    receipt={'schema':'CLEAR_TASK_PASSIVE_EVENT_CAPTURE_V1','run_id':run_id,'category':category,
             'selection':'FIRST_LOG_EVENT_OBSERVED_BY_THIS_MONITOR_ONE_ATTEMPT_PER_RUN_CATEGORY',
             'trigger_event':snapshot['triggers'][category], 'first_observed_local':now(),
             'logs_before':load_snapshot(root),'display':':1','status':'PENDING',
             'pixel_log_frame_alignment':'NOT_PROVEN_SCREENSHOT_IS_LATER_WALL_TIME_SAMPLING',
             'TASK_UPDATE_definition':'FIRST_CLEAR_TASK_RECEIVED_INCLUDING_D1_INITIAL_SUPPLY_NOT_FAKE_ASK_OR_ANSWER',
             'FIRST_COLLISION_definition':snapshot['first_collision_scope'],
             'new_model_pid_planner_control_world_tick_calls':0,
             'window_position_renderer_ego_spectator_changes':0}
    trigger=receipt['trigger_event']
    control_before=receipt['logs_before']['logs'].get('control_plan') or {}
    receipt['trigger_source_frame']=trigger.get('frame',trigger.get('collision_frame'))
    receipt['trigger_source_simulation_time_s']=trigger.get('simulation_time_s',trigger.get('collision_timestamp_s'))
    receipt['latest_logged_control_frame_before_capture']=control_before.get('frame')
    receipt['latest_logged_control_simulation_time_s_before_capture']=control_before.get('simulation_time_s')
    receipt['capture_time_interpretation']='CURRENT_WINDOW_SAMPLE_TRIGGER_MAY_PRECEDE_MONITOR_START_NEVER_BACKDATED_TO_EVENT'
    try:
        if receipt['logs_before']['current_run']!=run_id:
            raise RuntimeError('STATE current_run changed before capture')
        window=discover(snapshot['manifest_run'])
        receipt['window']=window
        before_identity=window['ownership']['native_identity']
        receipt['capture_started_local']=now(); start=time.perf_counter()
        destination=directory/'carla_window.png'
        capture(window,destination)
        receipt['capture_finished_local']=now();receipt['capture_wall_s']=time.perf_counter()-start
        receipt['image']={'path':str(destination.resolve()),'sha256':hashlib.sha256(destination.read_bytes()).hexdigest()}
        after=process_identity(before_identity['pid']) if capture is capture_x11_window else before_identity
        receipt['same_native_pid_starttime_after']=after['starttime_ticks']==before_identity['starttime_ticks']
        receipt['status']='CAPTURED'
    except Exception as exc:
        receipt['status']='SKIPPED_OR_CAPTURE_FAILED_NO_RETRY'
        receipt['error']=type(exc).__name__+': '+str(exc)
    receipt['logs_after']=load_snapshot(root)
    receipt['run_unchanged_during_capture']=receipt['logs_after']['current_run']==run_id
    receipt['completed_local']=now()
    dump(directory/'RECEIPT.json',receipt)
    return {'status':receipt['status'],'category':category,'directory':str(directory)}


def compact(value, limit=2400):
    if value is None:
        return 'UNKNOWN'
    text=json.dumps(value,ensure_ascii=False,indent=2) if not isinstance(value,str) else value
    return text if len(text)<=limit else text[:limit]+'\n[DISPLAY TRUNCATED; ORIGINAL LOG PRESERVED]'


def panel_text(snapshot, captures):
    state=snapshot['state'];run=snapshot.get('manifest_run') or {};logs=snapshot['logs']
    control=logs.get('control_plan') or {};model=logs.get('model_input') or {}
    diag=(snapshot.get('config') or {}).get('diagnostic') or {}
    lines=['run: '+str(snapshot.get('current_run') or 'UNKNOWN'),
           'state: '+str(state.get('status','UNKNOWN')),
           'condition: '+{'CLEAR_FROM_START':'D1 / CLEAR_FROM_START',
                          'CLEAR_AT_ANCHOR':'D2 / CLEAR_AT_ANCHOR'}.get(
                               run.get('condition',diag.get('condition')), 'UNKNOWN'),
           'sample wall: '+snapshot['sampled_local'],
           'control source frame/time: '+str(control.get('frame','UNKNOWN'))+' / '+str(control.get('simulation_time_s','UNKNOWN')),
           'task epoch: '+str(control.get('task_epoch',model.get('task_epoch','UNKNOWN'))),
           '\nConfigured clear task:\n'+compact(diag.get('clear_instruction'),800),
           '\nActually logged model language:\n'+compact(model.get('actual_model_language'),1400),
           '\nLatest clear task receipt:\n'+compact(snapshot.get('latest_task_received'),1500),
           '\nLatest CLEAR_TASK_EVENTS:\n'+compact(logs.get('task_events'),1600),
           '\nNative action/status/control:\n'+compact({key:control.get(key,'UNKNOWN') for key in
               ('action','status','steer','throttle','brake','hand_brake','reverse','task_received','task_route_proposed')},1000),
           '\nActive route identity:\n'+compact(control.get('active_route_identity'),800),
           '\nCandidate/resolved route evidence:\n'+compact(snapshot.get('latest_route_binding'),1600),
           '\nNative plan / target (already logged):\n'+compact({'native_plan':control.get('native_plan'),
                'target_point':control.get('target_point')},1400),
           '\nNative counters / diagnostic extra calls:\n'+compact({key:control.get(key,'UNKNOWN') for key in
                ('native_model_forward_count','control_return_count','model_calls_by_diagnostic',
                 'PID_calls_by_diagnostic','planner_steps_by_diagnostic','control_writes_by_diagnostic')},1000),
           '\nFirst ego collision callback (accepted criterion event shown separately):\n'+compact(snapshot['triggers'].get('FIRST_COLLISION'),1600),
           '\nCapture attempts:\n'+compact(captures,1000),
           '\nPixels are sampled after event observation; exact image/log frame equality is NOT PROVEN.']
    if snapshot.get('scope_error'):
        lines.insert(0,'UNKNOWN: '+snapshot['scope_error'])
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--once',action='store_true',help='Save a log-only view; no Tk/X11/capture')
    args=parser.parse_args();root=args.report_root.resolve();output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    if args.once:
        path=output/('LOG_ONLY_VIEW_'+str(time.time_ns())+'.json')
        dump(path,current_snapshot(root));print(path);return
    if os.environ.get('DISPLAY')!=':1':
        raise SystemExit('Panel and screenshots require local DISPLAY=:1')
    import tkinter as tk
    from tkinter.scrolledtext import ScrolledText
    window=tk.Tk();window.title('DriveClarify Clear Task — RESEARCH DEBUG VIEW')
    window.geometry('740x1240+1800+50');window.configure(bg='#15202b')
    for text in LABELS:
        tk.Label(window,text=text,fg='#ffcc66',bg='#15202b',font=('Sans',11,'bold')).pack(anchor='w',padx=12,pady=3)
    body=ScrolledText(window,wrap='word',bg='#15202b',fg='#e8edf2',insertbackground='white',font=('Monospace',10))
    body.pack(fill='both',expand=True,padx=10,pady=8)
    captures={};session=output/('PANEL_SESSION_'+str(time.time_ns())+'.json')
    dump(session,{'started_local':now(),'pid':os.getpid(),'display':':1','argv':list(os.sys.argv),
                  'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'labels':LABELS,'selection':EVENT_CATEGORIES,'no_service_or_episode_started':True})
    def refresh():
        try:
            snapshot=current_snapshot(root);run_id=snapshot.get('current_run')
            for category in EVENT_CATEGORIES:
                key=(run_id,category)
                if category in snapshot.get('triggers',{}) and key not in captures:
                    captures[key]=capture_event(root,output,snapshot,category)
            relevant={category:value for (run,category),value in captures.items() if run==run_id}
            value=panel_text(snapshot,relevant)
        except Exception as exc:
            value='OBSERVER VIEW ERROR — '+type(exc).__name__+': '+str(exc)+'\nNo simulator action was taken.'
            with (output/'PANEL_ERRORS.jsonl').open('a') as stream:
                stream.write(json.dumps({'wall':now(),'error':value})+'\n')
        scroll=body.yview();body.configure(state='normal');body.delete('1.0','end');body.insert('1.0',value)
        body.configure(state='disabled');body.yview_moveto(scroll[0]);window.after(1000,refresh)
    refresh();window.mainloop()


if __name__=='__main__':
    main()
