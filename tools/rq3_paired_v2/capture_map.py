#!/usr/bin/env python3
"""V2 非正式地图资格探查；无 ego、无驾驶模型、无真意分配。"""
import json
import math
import pathlib
import queue
import time
import xml.etree.ElementTree as ET
import carla

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / 'reports/driveclarify_rq3_paired_comparison_v2_task_binding_qualification_and_execution/qualification'
ROUTE = ROOT.parent / 'simlingo/leaderboard/data/bench2drive_split/bench2drive_145.xml'


def xyz(v):
    return [v.x, v.y, v.z]


def main():
    client = carla.Client('127.0.0.1', 28740)
    client.set_timeout(8)
    failures = []
    for _ in range(30):
        try:
            world = client.get_world()
            break
        except RuntimeError as e:
            failures.append(str(e))
            time.sleep(1)
    else:
        raise RuntimeError('MAP_PROBE_NO_SERVER')
    client.set_timeout(90)
    if world.get_map().name.split('/')[-1] != 'Town03':
        world = client.load_world('Town03')
    world.set_weather(carla.WeatherParameters.ClearNoon)
    m = world.get_map()
    r = ET.parse(ROUTE).getroot().find('route')
    points = [carla.Location(**{k: float(v) for k, v in x.attrib.items()}) for x in r.findall('./waypoints/position')]
    rows = []
    for i, loc in enumerate(points):
        w = m.get_waypoint(loc)
        p = w.get_right_lane()
        rows.append({'index': i, 'official_xyz': xyz(loc), 'driving_xyz': xyz(w.transform.location), 'driving_yaw': w.transform.rotation.yaw, 'road_id': w.road_id, 'lane_id': w.lane_id, 's': w.s, 'driving_width': w.lane_width, 'parking_xyz': xyz(p.transform.location) if p else None, 'parking_type': str(p.lane_type) if p else None, 'parking_width': p.lane_width if p else None, 'parking_yaw': p.transform.rotation.yaw if p else None})
    buildings = []
    for obj in world.get_environment_objects(carla.CityObjectLabel.Buildings):
        loc = obj.transform.location
        distance = min(math.hypot(loc.x - p.x, loc.y - p.y) for p in points)
        if distance <= 70:
            bb = obj.bounding_box
            buildings.append({'id': obj.id, 'name': obj.name, 'transform_xyz': xyz(loc), 'yaw': obj.transform.rotation.yaw, 'bbox_location': xyz(bb.location), 'bbox_extent': xyz(bb.extent), 'bbox_rotation': [bb.rotation.pitch, bb.rotation.yaw, bb.rotation.roll], 'distance_to_official_route_m': distance})
    blueprint_ids = sorted(x.id for x in world.get_blueprint_library() if any(t in x.id for t in ['cone', 'sprinter', 'building', 'kiosk', 'sign', 'barrier']))
    captures = []
    for index in [0, 24, 32, 50]:
        w = m.get_waypoint(points[index])
        bp = world.get_blueprint_library().find('sensor.camera.rgb')
        bp.set_attribute('image_size_x', '1280')
        bp.set_attribute('image_size_y', '720')
        bp.set_attribute('fov', '110')
        loc = points[index] + carla.Location(z=2.0)
        camera = world.spawn_actor(bp, carla.Transform(loc, w.transform.rotation))
        frames = queue.Queue()
        camera.listen(frames.put)
        try:
            im = frames.get(timeout=45)
            for _ in range(3):
                im = frames.get(timeout=45)
            path = OUT / ('MAP_FORWARD_INDEX_%02d.png' % index)
            im.save_to_disk(str(path))
            captures.append({'route_index': index, 'frame': im.frame, 'simulation_time': im.timestamp, 'path': str(path), 'camera_xyz': xyz(loc), 'camera_yaw': w.transform.rotation.yaw})
        finally:
            camera.stop()
            camera.destroy()
    receipt = {'scope': 'NON_FORMAL_MAP_ONLY_NO_EGO_NO_POLICY_RUN', 'route': str(ROUTE), 'map': m.name, 'server_version': client.get_server_version(), 'client_version': client.get_client_version(), 'route_rows': rows, 'buildings': buildings, 'available_blueprints': blueprint_ids, 'captures': captures, 'infrastructure_connection_errors': failures, 'native_development_runs': 0, 'model_forwards': 0, 'formal_exposures': 0, 'seeds_used': []}
    (OUT / 'MAP_PROBE_RECEIPT.json').write_text(json.dumps(receipt, indent=2) + '\n')
    (OUT / 'Town03_server.xodr').write_text(m.to_opendrive())
    print(json.dumps({'map': m.name, 'buildings': len(buildings), 'captures': captures, 'blueprints': blueprint_ids}, indent=2), flush=True)


if __name__ == '__main__':
    main()
