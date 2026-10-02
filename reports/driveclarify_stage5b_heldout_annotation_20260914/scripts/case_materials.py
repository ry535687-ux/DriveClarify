"""仅构造静态题面。设计分层仅由管理员读取，不生成关系判断。"""

from copy import deepcopy
from random import Random


# 10/10/10 是作者的覆盖计划，不是答案或标注配额。
# 不计算、校验或写入任何逐题关系答案。
SPECS = [
    ("Referential", "shared_interval", "silver cargo bicycle", "with a wicker basket", "with a folded trailer", "after"),
    ("Referential", "shared_interval", "teal delivery locker", "with a round emblem", "with a square emblem", "before"),
    ("Referential", "shared_interval", "cream utility cabinet", "beside the stone post", "beside the timber post", "after"),
    ("Landmark", "shared_interval", "ceramic workshop", "with a striped awning", "with a plain awning", "after"),
    ("Landmark", "shared_interval", "botanical bookshop", "with an arched doorway", "with a rectangular doorway", "before"),
    ("Landmark", "shared_interval", "bicycle repair shop", "with a copper sign", "with a slate sign", "after"),
    ("Order / ordinal", "shared_interval", "mosaic arch", "", "", "ordinal"),
    ("Order / ordinal", "shared_interval", "blue information board", "", "", "ordinal"),
    ("Execution-location", "shared_interval", "striped canopy", "", "", "after_extent"),
    ("Execution-location", "shared_interval", "copper mesh fence", "", "", "before_extent"),
    ("Referential", "separate_interval", "ochre parcel box", "with two slots", "with one slot", "after"),
    ("Referential", "separate_interval", "maroon handcart", "under a tree", "beside a bench", "before"),
    ("Referential", "separate_interval", "white equipment trailer", "with a circular reflector", "with a triangular reflector", "after"),
    ("Landmark", "separate_interval", "clock repair shop", "with a green door", "with a burgundy door", "after"),
    ("Landmark", "separate_interval", "weaving studio", "with a hanging pennant", "with a wall plaque", "before"),
    ("Order / ordinal", "separate_interval", "stone drinking fountain", "", "", "ordinal"),
    ("Order / ordinal", "separate_interval", "yellow visitor kiosk", "", "", "ordinal"),
    ("Order / ordinal", "separate_interval", "timber notice frame", "", "", "ordinal"),
    ("Execution-location", "separate_interval", "glass arcade", "", "", "after_extent"),
    ("Execution-location", "separate_interval", "painted retaining wall", "", "", "before_extent"),
    ("Referential", "partial_record", "violet storage chest", "beside a flagpole", "beside a lamppost", "after"),
    ("Referential", "partial_record", "lime service box", "with a ribbed lid", "with a smooth lid", "branch"),
    ("Landmark", "partial_record", "printmaking studio", "with a terracotta sign", "with a turquoise sign", "before"),
    ("Landmark", "partial_record", "tea merchant", "", "", "completion"),
    ("Landmark", "partial_record", "instrument maker", "", "", "completion"),
    ("Order / ordinal", "partial_record", "granite memorial post", "", "", "ordinal"),
    ("Order / ordinal", "partial_record", "red community noticeboard", "", "", "ordinal"),
    ("Execution-location", "partial_record", "fabric market awning", "", "", "after_extent"),
    ("Execution-location", "partial_record", "reed screen", "", "", "before_extent"),
    ("Execution-location", "partial_record", "pottery hall entrance", "", "", "branch"),
]

PUBLIC_CONTEXT = (
    "本题是静态道路任务示意，保留已有道路地图的一段连接结构，使用本题局部编号。"
    "参照物、招牌、线位及其位置是本题新设的静态情景事实，并非实地观测。"
    "局部编号不表示全地图编号，不同题的同名编号互不关联。"
    "仅依据本题提供的事实，不补充其他地图知识。"
)


def evidence(ref, location, branch, completion):
    return dict(referent_description=ref, junction_or_execution_location=location,
                road_branch_description=branch, task_completion_obligation=completion)


def make_case(spec, motif, author_index):
    family, stratum, obj, attr_a, attr_b, mode = spec
    # 所有题使用一致的基本任务语义；源地图距离只用于描述，不定义完成条件。
    geometry = (
        "沿指定主路前行依次遇到 J1、J2、J3，起点位于 J1 之前。"
        "每个 Ji 有一条本题指定侧路 Qi，侧路入口横线为 Gi；Gi 不是停车点。"
        "主路在 J1→J2 和 J2→J3 的连接道路段长度分别约为 "
        f"{motif['between_lengths_m'][0]:.1f} m 和 {motif['between_lengths_m'][1]:.1f} m"
        "（不含路口内部连接段）。除场景卡明确列出的补充或记录缺口外，"
        "本题侧路清单为 Q1、Q2、Q3；没有未提出的附加通行要求。"
    )
    branch = "在选定的 Ji 离开主路并进入该处的 Qi。"
    completion = "沿主路到选定路口，转入 Qi 并越过其入口线 Gi 即完成；没有后续地点或停车义务。"
    extra = "所有物体在题设期间保持静止；不需要预测车辆或行人运动。"
    private = dict(author_index=author_index, family=family, construction_coverage=stratum,
                   construction_mode=mode, new_reference_combination=[obj, attr_a, attr_b],
                   missing_material=None, source_motif=motif)

    if mode in ("after", "before"):
        relation = "之后遇到的第一个路口" if mode == "after" else "之前遇到的最后一个路口"
        instruction = (f"Take the first side road after the {obj}." if mode == "after"
                       else f"Take the last side road before the {obj}.")
        ia = f"Use the {obj} {attr_a} as the reference; take the {'first side road after' if mode == 'after' else 'last side road before'} it."
        ib = f"Use the {obj} {attr_b} as the reference; take the {'first side road after' if mode == 'after' else 'last side road before'} it."
        if stratum == "shared_interval":
            pa, pb = ("J1 与 J2 之间，靠近 J1", "J1 与 J2 之间，靠近 J2") if mode == "after" else ("J2 与 J3 之间，靠近 J2", "J2 与 J3 之间，靠近 J3")
        elif stratum == "separate_interval":
            pa, pb = ("起点与 J1 之间", "J2 与 J3 之间") if mode == "after" else ("J1 与 J2 之间", "J2 与 J3 之间")
        else:
            pa = "J1 与 J2 之间" if mode == "after" else "J2 与 J3 之间"
            pb = None
            private['missing_material'] = "第二个参照物与路口序列的对应位置；其物体身份已给出。"
        ra = f"{obj} {attr_a}；静态位置：{pa}。"
        rb = f"{obj} {attr_b}；静态位置：{pb}。" if pb else f"{obj} {attr_b}；物体清单记载其存在，位置记录未包含在场景卡中。"
        scene = f"可供指代的两个物体为 {obj} {attr_a} 和 {obj} {attr_b}。二者都在本题主路旁。" + extra
        ca, cb = evidence(ra, relation, branch, completion), evidence(rb, relation, branch, completion)
    elif mode == "ordinal":
        instruction = f"Use the second turn after the {obj}."
        ia = f"Count distinct road junctions after the {obj}; use the side road at the second junction."
        ib = f"Count side-road entrances after the {obj}; use the second entrance encountered."
        if stratum == "shared_interval":
            detail = "J1、J2、J3 各有且仅有一个侧路入口。"
        elif stratum == "separate_interval":
            detail = "J1 是一个路口，其主路通行方向先后经过 Q1 和 S1 两个不同侧路口，二者都属于 J1；它们通向不同道路。J2、J3 各有一个侧路入口。S1 的入口线为 H1。"
        else:
            detail = "J2、J3 各有一个侧路入口。J1 的记录确认存在 Q1；J1 侧路口清单的其余部分未收录，未说明是否还存在另一入口，也未说明它与 Q1 的先后顺序。"
            private['missing_material'] = "J1 处完整侧路口数量与顺序；会影响数路口与数入口的执行位置。"
        scene = f"{obj} 在起点与 J1 之间，起点至 J1 无其他路口。{detail}" + extra
        ca = evidence(f"{obj}，位于 J1 之前。", "按 J1、J2、J3 三个不同路口计数，取第二个。", "使用所选路口的 Q 侧路。", completion)
        cb = evidence(f"{obj}，位于 J1 之前。", "按沿主路实际经过的侧路入口计数，取第二个；同一路口内不同入口分别计数。", "进入第二个遇到的侧路口所通向的道路。", "进入被选侧路并越过该侧路入口线即完成；没有后续地点或停车义务。")
        private['requires_extra_side_at_J1'] = stratum != 'shared_interval'
    elif mode.endswith("_extent"):
        after = mode == 'after_extent'
        instruction = f"Take the {'first side road after' if after else 'last side road before'} the {obj}."
        ia = f"Take the {'first side road after' if after else 'last side road before'} the approach-side edge of the {obj}."
        ib = f"Take the {'first side road after' if after else 'last side road before'} the far edge of the {obj} along the direction of travel."
        if stratum == 'shared_interval':
            a, b = ("J1 与 J2 之间，尚未到 J2", "J1 与 J2 之间，且在近端之后、J2 之前") if after else ("J2 与 J3 之间", "J2 与 J3 之间，且在近端之后、J3 之前")
        elif stratum == 'separate_interval':
            a, b = "J1 与 J2 之间", "J2 与 J3 之间"
        else:
            a, b = "J1 与 J2 之间", None
            private['missing_material'] = "沿行进方向的远端边界与 J2 的先后关系。"
        scene = f"{obj} 沿主路延伸，近端位置为{a}；" + (f"远端位置为{b}。" if b else "场景卡未记录远端位于哪个路口区间。") + "路口指侧路与主路的交会线；物体边界本身不算路口。" + extra
        phrase = "之后的第一个路口" if after else "之前的最后一个路口"
        ca = evidence(f"{obj} 的迎车近端边界。", f"从近端边界确定{phrase}。", branch, completion)
        cb = evidence(f"{obj} 的顺行远端边界。", f"从远端边界确定{phrase}。", branch, completion)
    elif mode == 'branch':
        instruction = f"Enter the lane by the {obj}."
        if family == 'Referential':
            ia = f"Enter the lane beside the {obj} {attr_a}."
            ib = f"Enter the lane beside the {obj} {attr_b}."
            refs = [f"{obj} {attr_a}", f"{obj} {attr_b}"]
        else:
            ia = f"Use the lane beside the approach-side edge of the {obj}."
            ib = f"Use the lane beside the far edge of the {obj}."
            refs = [f"{obj} 的迎车近端", f"{obj} 的顺行远端"]
        scene = f"{refs[0]} 和 {refs[1]} 都在 J1 路口范围。J1 的 Q1 与 S1 是不同侧路，入口线分别 G1 与 H1；两侧路从主路可达。{refs[0]} 邻接 Q1 的入口。{refs[1]} 邻接哪一个入口的对应记录未收录。" + extra
        ca = evidence(refs[0], "J1 路口范围。", "Q1，入口线 G1。", "进入参照物邻接的侧路并越过其入口线即完成；无后续任务。")
        cb = evidence(refs[1], "J1 路口范围。", None, "进入参照物邻接的侧路并越过其入口线即完成；无后续任务。")
        private['missing_material'] = "第二个参照位置与 Q1 / S1 的邻接对应；同在 J1 不能据此推断同一道路。"
        private['requires_extra_side_at_J1'] = True
    elif mode == 'completion':
        instruction = f"Take the side road at the {obj} and follow it to the end of its frontage."
        ia = f"Treat the public entrance court as the {obj}'s frontage; finish when passing its end."
        ib = f"Treat the whole building frontage as the {obj}'s frontage; finish when passing its end."
        scene = f"{obj} 位于 J2，唯一相邻侧路是 Q2。Q2 上有入口线 G2，前方依次有横线 F2、T2；三条线位置不同。商铺公共入口庭院的沿路边界终止于 F2。整栋建筑临街面的终止线记录未包含在本题材料中。" + extra
        ca = evidence(f"{obj} 的公共入口庭院临街面。", "在 J2 转入 Q2。", "Q2。", "沿 Q2 前行并越过公共入口庭院的终止线 F2，达到该解释指定的完成位置。")
        cb = evidence(f"{obj} 的整栋建筑临街面。", "在 J2 转入 Q2。", "Q2。", None)
        # 未知的是完成线的地图绑定，解释本身仍明确表达完整任务义务。
        ib += " Continue on the side road until that boundary is passed."
        private['missing_material'] = "整栋建筑临街面终点对应哪一条线；可与 F2 重合，也可位于更远位置。"
    else:
        raise ValueError(mode)

    case = dict(passenger_instruction=instruction, interpretation_A=ia, interpretation_B=ib,
                scene_description=scene, task_evidence=dict(candidate_A=ca, candidate_B=cb),
                map_context=dict(public_description=PUBLIC_CONTEXT, optional_static_geometry_summary=geometry))
    return case, private


def construct(motifs):
    # 无语义 ID 也要打散作者分层顺序。按固定种子分配，未使用任何方法输出。
    assignments = list(range(len(SPECS)))
    Random(510031).shuffle(assignments)
    pairs = []
    used = set()
    for ordinal, source_index in enumerate(assignments, 1):
        spec = SPECS[source_index]
        needs_four = spec[5] == 'branch' or (spec[5] == 'ordinal' and spec[1] != 'shared_interval')
        motif = next(m for m in motifs if tuple(m['junction_path'][1:4]) not in used
                     and all(length >= 15 for length in m['between_lengths_m'])
                     and (not needs_four or len(m['side_roads'][0]) >= 2))
        used.add(tuple(motif['junction_path'][1:4]))
        case, prov = make_case(spec, deepcopy(motif), source_index + 1)
        cid = f'HOLDOUT_{ordinal:03d}'
        # A/B 候选朝向也固定随机交换，以免 A 成为默认答案线索。
        if Random(510100 + ordinal).getrandbits(1):
            case['interpretation_A'], case['interpretation_B'] = case['interpretation_B'], case['interpretation_A']
            ca, cb = case['task_evidence']['candidate_A'], case['task_evidence']['candidate_B']
            case['task_evidence'] = dict(candidate_A=cb, candidate_B=ca)
            prov['candidate_order_swapped'] = True
        else:
            prov['candidate_order_swapped'] = False
        pairs.append((dict(case_id=cid, **case), dict(case_id=cid, **prov)))
    return [x[0] for x in pairs], [x[1] for x in pairs]
