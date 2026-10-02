"""查清 route_polyline_world 的真实形状：是否含尖刺、18.7m 处是否真的右转。只读。"""
import glob
import json
import math
import os

SUITE = ("/home/buaa/wrh/DriveClarify/artifacts/"
         "driveclarify_method_v1_visual_behavioral_acceptance_suite_v1")


def main():
    for letter, name in (("c", "DCVA1-04-WAIT-0815"),
                         ("a", "DCVA1-02-AS-0815")):
        j = json.load(open(glob.glob(os.path.join(
            SUITE, name, "*LIVE_RECEIPT.json"))[0]))
        poly = j["topology_context"]["route_polyline_world"]
        s = [0.0]
        for i in range(1, len(poly)):
            s.append(s[-1] + math.hypot(poly[i][0] - poly[i - 1][0],
                                        poly[i][1] - poly[i - 1][1]))
        print(f"\n{'='*70}\n({letter}) {name}  {len(poly)} 点  总长 {s[-1]:.2f} m")
        print(f"  source = {j['topology_context']['source']}")
        print("  idx    arc      x         y       段长    逐点转向")
        for i in range(len(poly)):
            seg = (s[i] - s[i - 1]) if i else 0.0
            turn = ""
            if 0 < i < len(poly) - 1:
                a = math.atan2(poly[i][1] - poly[i - 1][1],
                               poly[i][0] - poly[i - 1][0])
                b = math.atan2(poly[i + 1][1] - poly[i][1],
                               poly[i + 1][0] - poly[i][0])
                dd = math.degrees(math.atan2(math.sin(b - a), math.cos(b - a)))
                if abs(dd) > 5:
                    turn = f"  <<< {dd:+.1f}deg"
            # 只打印首尾和转角附近，避免刷屏
            if i < 3 or i > len(poly) - 4 or turn or abs(s[i] - 18.72) < 3 \
                    or abs(s[i] - 57.05) < 3 or abs(s[i] - 14.72) < 3:
                print(f"  {i:3d}  {s[i]:7.2f}  {poly[i][0]:8.3f} "
                      f"{poly[i][1]:8.3f}  {seg:5.2f}{turn}")


if __name__ == "__main__":
    main()
