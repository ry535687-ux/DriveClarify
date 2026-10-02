"""验证 ego-local 计划 -> 世界 -> 航线投影 的符号约定，并检查航线是否真的转弯。

自校验逻辑：正确的符号约定应当让"沿航线行驶的计划"投影后横向偏差很小。
两种约定各算一次，取横向偏差小的那个；若两者都大，则说明不能用此法作图。
只读。
"""
import glob
import json
import math
import os

ROOT = "/home/buaa/wrh/DriveClarify"
SUITE = os.path.join(
    ROOT, "artifacts",
    "driveclarify_method_v1_visual_behavioral_acceptance_suite_v1")
CASES = {"a": "DCVA1-02-AS-0815", "b": "DCVA1-03-ASK-0815",
         "c": "DCVA1-04-WAIT-0815", "e": "DCVA1-06-TL-0815",
         "f": "DCVA1-01-ACT-0815"}


def arc_lengths(poly):
    s = [0.0]
    for i in range(1, len(poly)):
        s.append(s[-1] + math.hypot(poly[i][0] - poly[i - 1][0],
                                    poly[i][1] - poly[i - 1][1]))
    return s


def project(poly, s, pt):
    best = (float("inf"), 0.0)
    for i in range(len(poly) - 1):
        ax, ay = poly[i]
        vx, vy = poly[i + 1][0] - ax, poly[i + 1][1] - ay
        L2 = vx * vx + vy * vy
        if L2 <= 0:
            continue
        t = max(0.0, min(1.0, ((pt[0] - ax) * vx + (pt[1] - ay) * vy) / L2))
        px, py = ax + t * vx, ay + t * vy
        dd = math.hypot(pt[0] - px, pt[1] - py)
        if dd < best[0]:
            best = (dd, s[i] + t * math.sqrt(L2))
    return best


def to_world(ego, fwd_m, lat_m, lat_sign):
    f = ego["forward_vector_xyz"]
    r = ego["right_vector_xyz"]
    o = ego["location_xyz"]
    return (o[0] + f[0] * fwd_m + lat_sign * r[0] * lat_m,
            o[1] + f[1] * fwd_m + lat_sign * r[1] * lat_m)


def main():
    for letter, name in CASES.items():
        d = os.path.join(SUITE, name)
        j = json.load(open(glob.glob(
            os.path.join(d, "*LIVE_RECEIPT.json"))[0]))
        poly = (j.get("topology_context") or {}).get("route_polyline_world")
        if not poly:
            continue
        s = arc_lengths(poly)
        rows = [json.loads(x) for x in open(
            os.path.join(d, "post_hoc_world_state.jsonl"))]
        egos = {r.get("gametime_frame"): r.get("ego") for r in rows}

        print(f"\n{'='*72}\n({letter}) {name}   航线总长 {s[-1]:.2f} m")

        # 航线自身是否转弯：比较起点与终点朝向
        h0 = math.degrees(math.atan2(poly[1][1] - poly[0][1],
                                     poly[1][0] - poly[0][0]))
        hN = math.degrees(math.atan2(poly[-1][1] - poly[-2][1],
                                     poly[-1][0] - poly[-2][0]))
        # 找最大逐点朝向变化位置
        turns = []
        for i in range(1, len(poly) - 1):
            a = math.atan2(poly[i][1] - poly[i - 1][1],
                           poly[i][0] - poly[i - 1][0])
            b = math.atan2(poly[i + 1][1] - poly[i][1],
                           poly[i + 1][0] - poly[i][0])
            dd = math.degrees(math.atan2(math.sin(b - a), math.cos(b - a)))
            turns.append((abs(dd), s[i], dd))
        turns.sort(reverse=True)
        print(f"    航线朝向 {h0:.1f}deg -> {hN:.1f}deg  "
              f"净转向 {((hN - h0 + 180) % 360) - 180:+.1f}deg")
        print(f"    最大逐点转向: " + ", ".join(
            f"{t[2]:+.1f}deg @ {t[1]:.1f}m" for t in turns[:3]))

        plans = []
        for r in (j.get("candidate_plan_repetitions") or []):
            plans.append(("candidate", r["model_predicted_local_route"],
                          r.get("source_frame_id")))
        fr = j.get("fresh_replan")
        if isinstance(fr, dict) and fr.get("route"):
            plans.append(("post_answer", fr["route"],
                          j.get("fresh_replan_source_frame_id")))

        for kind, route, sf in plans:
            ego = egos.get(sf)
            if not ego:
                print(f"    {kind} src_f={sf}: world_state 无该帧，跳过")
                continue
            for sign, tag in ((+1, "+right"), (-1, "-right")):
                devs, progs = [], []
                for fwd, lat in route:
                    w = to_world(ego, fwd, lat, sign)
                    dev, prog = project(poly, s, w)
                    devs.append(dev)
                    progs.append(prog)
                print(f"    {kind} src_f={sf} lat_sign={tag}: "
                      f"progress {progs[0]:.2f}->{progs[-1]:.2f} m, "
                      f"横向偏差 mean={sum(devs)/len(devs):.3f} "
                      f"max={max(devs):.3f} m")


if __name__ == "__main__":
    main()
