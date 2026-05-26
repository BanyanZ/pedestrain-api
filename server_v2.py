#!/usr/bin/env python3
"""
Nuclear Facility Pedestrian Density Prediction Server v2
- Watches three folders: raw images / BEV images / scene graph JSONs
- Matches files by stem (000000.jpg <-> 000000_intersection.png <-> 000000_scene_graph.json)
- Serves real-time analysis via REST API
- Pure Python stdlib, no extra dependencies
Run: python server_v2.py --images ./images --bev ./bev --graphs ./graphs [--port 8765]
"""

import json, time, threading, argparse, mimetypes, base64
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Nuclear Facility Flow Density Engine
# ─────────────────────────────────────────────────────────────────────────────

LEVEL_COLOR = {"SAFE":"#1D9E75","CAUTION":"#1F73B7","SLOW":"#BA7517","STOP":"#E24B4A"}
LEVEL_BG    = {"SAFE":"#EAF3DE","CAUTION":"#E6F1FB","SLOW":"#FAEEDA","STOP":"#FCEBEB"}
LEVEL_NAME  = {"SAFE":"正常", "CAUTION":"关注", "SLOW":"限流", "STOP":"停入"}

PERSON_TYPES = {
    "PERSON", "PEDESTRIAN", "WORKER", "STAFF", "EMPLOYEE", "VISITOR",
    "CONTRACTOR", "OPERATOR", "GUARD", "RESPONDER", "TECHNICIAN",
}

DEFAULT_WEIGHTS = (0.35, 0.25, 0.25, 0.15)
DEFAULT_HORIZON_MINUTES = 5.0

ZONE_RULES = {
    "REACTOR":          {"label":"反应堆厂房", "area":36.0, "target":0.16, "limit":0.38, "weight":1.60},
    "RADIATION":        {"label":"辐射控制区", "area":32.0, "target":0.18, "limit":0.45, "weight":1.50},
    "CONTROLLED":       {"label":"受控区",     "area":45.0, "target":0.25, "limit":0.60, "weight":1.30},
    "ACCESS_GATE":      {"label":"门禁/闸机",  "area":10.0, "target":0.40, "limit":1.00, "weight":1.25},
    "DECON":            {"label":"去污/监测点", "area":14.0, "target":0.22, "limit":0.55, "weight":1.45},
    "EVACUATION_ROUTE": {"label":"疏散通道",   "area":24.0, "target":0.28, "limit":0.70, "weight":1.35},
    "EXIT":             {"label":"安全出口",   "area":12.0, "target":0.35, "limit":0.90, "weight":1.35},
    "STAIR":            {"label":"楼梯间",     "area":14.0, "target":0.28, "limit":0.70, "weight":1.35},
    "MUSTER":           {"label":"集合点",     "area":120.0,"target":0.70, "limit":1.60, "weight":0.80},
    "CORRIDOR":         {"label":"通道",       "area":24.0, "target":0.35, "limit":0.85, "weight":1.00},
    "CONTROL_ROOM":     {"label":"主控/值守区", "area":28.0, "target":0.20, "limit":0.50, "weight":1.35},
    "GENERAL":          {"label":"普通作业区", "area":60.0, "target":0.45, "limit":1.10, "weight":0.90},
}

HIGHER_PRIORITY_ZONE = {
    "REACTOR": 90, "RADIATION": 80, "CONTROL_ROOM": 75, "DECON": 70,
    "CONTROLLED": 65, "EVACUATION_ROUTE": 60, "EXIT": 58, "STAIR": 55,
    "ACCESS_GATE": 50, "CORRIDOR": 35, "MUSTER": 25, "GENERAL": 10,
}

def _norm(value, default=""):
    if value is None:
        return default
    return str(value).strip().upper().replace("-", "_").replace(" ", "_")

def _as_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是", "已授权", "authorized"}

def _clamp(value, low=0.0, high=100.0):
    return max(low, min(high, value))

def _pick_number(*values, default=None):
    for value in values:
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default

def _is_person(subject_type):
    st = _norm(subject_type)
    return st in PERSON_TYPES or any(token in st for token in ("PERSON", "WORKER", "STAFF", "PEDESTRIAN"))

def _meta_dict(value):
    return value if isinstance(value, dict) else {}

def _state_is_present(state):
    return _norm(state).lower() in {"inside", "in", "on", "at", "near", "queued", "waiting", "entering", "exiting", ""}

def _infer_zone_type(raw_type="", zone_id="", meta=None):
    meta = _meta_dict(meta)
    candidates = [
        meta.get("zone_type"), meta.get("area_type"), meta.get("facility_zone"),
        meta.get("safety_zone"), raw_type, zone_id,
    ]
    text = " ".join(_norm(c) for c in candidates if c)

    if _as_bool(meta.get("is_reactor_building")) or "REACTOR" in text or "反应堆" in text:
        return "REACTOR"
    if _as_bool(meta.get("is_radiation_area")) or _as_bool(meta.get("radiation_controlled")) or "RADIATION" in text or "RAD" in text or "辐射" in text:
        return "RADIATION"
    if _as_bool(meta.get("is_control_room")) or "CONTROL_ROOM" in text or "主控" in text:
        return "CONTROL_ROOM"
    if _as_bool(meta.get("is_decon")) or "DECON" in text or "MONITOR" in text or "去污" in text or "监测" in text:
        return "DECON"
    if _as_bool(meta.get("is_evacuation_route")) or _as_bool(meta.get("evacuation_route")) or "EVAC" in text or "疏散" in text:
        return "EVACUATION_ROUTE"
    if _as_bool(meta.get("is_exit")) or "EXIT" in text or "出口" in text:
        return "EXIT"
    if "STAIR" in text or "楼梯" in text:
        return "STAIR"
    if _as_bool(meta.get("is_access_gate")) or "GATE" in text or "ACCESS" in text or "AIRLOCK" in text or "门禁" in text or "闸机" in text or "气闸" in text:
        return "ACCESS_GATE"
    if _as_bool(meta.get("is_muster")) or "MUSTER" in text or "ASSEMBLY" in text or "集合" in text:
        return "MUSTER"
    if "CORRIDOR" in text or "PASSAGE" in text or "HALLWAY" in text or "通道" in text or "走廊" in text:
        return "CORRIDOR"
    if _as_bool(meta.get("is_controlled")) or _as_bool(meta.get("restricted")) or "CONTROLLED" in text or "RESTRICTED" in text or "受控" in text:
        return "CONTROLLED"
    if any(token in text for token in ("ZONE", "AREA", "ROOM", "WORKSHOP", "BUILDING", "厂房", "区域", "车间", "房间")):
        return "GENERAL"
    return "GENERAL"

def _rule(zone_type):
    return ZONE_RULES.get(zone_type, ZONE_RULES["GENERAL"])

def _new_zone(zone_id, zone_type="GENERAL", meta=None):
    meta = dict(_meta_dict(meta))
    zone_type = zone_type or "GENERAL"
    rule = _rule(zone_type)
    return {
        "id": str(zone_id),
        "zone_type": zone_type,
        "label": meta.get("label") or meta.get("name") or rule["label"],
        "meta": meta,
        "persons": {},
        "unauthorized": set(),
        "missing_dosimeter": set(),
        "blocked": _as_bool(meta.get("blocked")),
        "opposite_flow": _as_bool(meta.get("opposite_flow")),
        "queue_count": _pick_number(meta.get("queue_count"), meta.get("waiting_count"), default=0.0) or 0.0,
    }

def _merge_zone_type(current, incoming):
    if HIGHER_PRIORITY_ZONE.get(incoming, 0) > HIGHER_PRIORITY_ZONE.get(current, 0):
        return incoming
    return current

def _top_level_zones(sg):
    zones = []
    for key in ("zones", "areas", "facility_zones", "regions"):
        value = sg.get(key)
        if isinstance(value, list):
            zones.extend(item for item in value if isinstance(item, dict))
    return zones

def collect_zones(sg):
    zones = {}

    for item in _top_level_zones(sg):
        zid = item.get("id") or item.get("zone_id") or item.get("name") or item.get("label")
        if not zid:
            continue
        ztype = _infer_zone_type(item.get("type") or item.get("object_type"), zid, item)
        zones[str(zid)] = _new_zone(zid, ztype, item)

    for t in sg.get("object_map_triples", []):
        if not isinstance(t, dict):
            continue
        meta = dict(_meta_dict(t.get("object_meta")))
        zid = t.get("object") or meta.get("id") or meta.get("name")
        if not zid:
            continue
        ztype = _infer_zone_type(t.get("object_type"), zid, meta)
        zone = zones.setdefault(str(zid), _new_zone(zid, ztype, meta))
        zone["zone_type"] = _merge_zone_type(zone["zone_type"], ztype)
        zone["label"] = meta.get("label") or meta.get("name") or zone["label"]
        zone["meta"].update(meta)
        zone["blocked"] = zone["blocked"] or _as_bool(meta.get("blocked")) or _norm(t.get("state")) == "BLOCKED"
        zone["opposite_flow"] = zone["opposite_flow"] or _as_bool(meta.get("opposite_flow"))
        zone["queue_count"] = max(zone["queue_count"], _pick_number(meta.get("queue_count"), t.get("queue_count"), default=0.0) or 0.0)

        if _is_person(t.get("subject_type")) and _state_is_present(t.get("state")):
            sid = str(t.get("subject") or f"person_{len(zone['persons']) + 1}")
            ratio = _as_float(t.get("inter_ratio"), 1.0)
            contribution = _clamp(ratio if ratio > 0 else 1.0, 0.05, 1.0)
            zone["persons"][sid] = max(zone["persons"].get(sid, 0.0), contribution)
            smeta = _meta_dict(t.get("subject_meta"))
            authorized = smeta.get("authorized", t.get("authorized"))
            if authorized is False or str(authorized).strip().lower() in {"false", "0", "no", "未授权"}:
                zone["unauthorized"].add(sid)
            dosimeter = smeta.get("dosimeter", smeta.get("has_dosimeter", t.get("has_dosimeter")))
            if dosimeter is False or str(dosimeter).strip().lower() in {"false", "0", "no", "未佩戴"}:
                zone["missing_dosimeter"].add(sid)

    return list(zones.values())

def _scene_context(sg):
    meta = {}
    for key in ("scene_meta", "metadata", "facility_meta", "context"):
        if isinstance(sg.get(key), dict):
            meta.update(sg[key])
    alarm = _norm(meta.get("alarm_state") or meta.get("emergency_level") or sg.get("alarm_state") or "NORMAL")
    phase = _norm(meta.get("operation_phase") or meta.get("plant_mode") or sg.get("operation_phase") or "NORMAL")
    shift_change = _as_bool(meta.get("shift_change") or sg.get("shift_change"))
    horizon = _pick_number(meta.get("horizon_minutes"), sg.get("horizon_minutes"), default=DEFAULT_HORIZON_MINUTES)
    return {"meta": meta, "alarm": alarm, "phase": phase, "shift_change": shift_change, "horizon_minutes": max(1.0, horizon)}

def _event_surge(zone_type, context):
    alarm = context["alarm"]
    phase = context["phase"]
    shift_change = context["shift_change"]
    surge = 0.0

    if alarm in {"ALERT", "SITE_AREA_EMERGENCY", "GENERAL_EMERGENCY", "EMERGENCY", "EVACUATION"}:
        if zone_type in {"EVACUATION_ROUTE", "EXIT", "STAIR", "DECON", "MUSTER", "ACCESS_GATE"}:
            surge += 0.35
        elif zone_type in {"REACTOR", "RADIATION", "CONTROLLED", "CONTROL_ROOM"}:
            surge -= 0.20
    if shift_change:
        if zone_type in {"ACCESS_GATE", "CORRIDOR", "GENERAL", "EXIT", "STAIR"}:
            surge += 0.18
        elif zone_type in {"CONTROLLED", "RADIATION"}:
            surge += 0.08
    if phase in {"OUTAGE", "REFUELING", "MAINTENANCE", "检修", "换料"}:
        if zone_type in {"CONTROLLED", "RADIATION", "REACTOR", "DECON", "ACCESS_GATE"}:
            surge += 0.15
    return surge

def _zone_numbers(zone, context):
    meta = zone["meta"]
    ztype = zone["zone_type"]
    rule = _rule(ztype)

    current_count = _pick_number(
        meta.get("current_count"), meta.get("person_count"), meta.get("count"), meta.get("occupancy"),
        default=None,
    )
    if current_count is None:
        current_count = sum(zone["persons"].values())
    current_count = max(0.0, current_count)

    area_m2 = _pick_number(meta.get("area_m2"), meta.get("area"), meta.get("size_m2"), default=rule["area"])
    area_m2 = max(1.0, area_m2)

    incoming = _pick_number(meta.get("incoming_rate_ppm"), meta.get("inflow_ppm"), meta.get("entry_rate_ppm"), default=0.0) or 0.0
    outgoing = _pick_number(meta.get("outgoing_rate_ppm"), meta.get("outflow_ppm"), meta.get("exit_rate_ppm"), default=0.0) or 0.0
    horizon = context["horizon_minutes"]
    surge_count = current_count * _event_surge(ztype, context)
    projected_count = max(0.0, current_count + (incoming - outgoing) * horizon + surge_count)

    density = _pick_number(meta.get("density"), meta.get("density_pm2"), default=current_count / area_m2)
    predicted_density = _pick_number(meta.get("predicted_density"), meta.get("forecast_density"), default=projected_count / area_m2)

    target_density = _pick_number(meta.get("target_density"), meta.get("target_density_pm2"), default=rule["target"])
    limit_density = _pick_number(meta.get("limit_density"), meta.get("max_density"), meta.get("capacity_density"), default=rule["limit"])
    limit_density = max(limit_density, target_density + 0.01)

    capacity_people = _pick_number(meta.get("capacity"), meta.get("capacity_people"), default=area_m2 * limit_density)
    target_people = area_m2 * target_density
    utilization = projected_count / max(capacity_people, 1.0)

    base_score = _clamp((predicted_density - target_density) / (limit_density - target_density) * 100.0)
    modifiers = 0.0
    if zone["blocked"]:
        modifiers += 18.0
    if zone["opposite_flow"]:
        modifiers += 10.0
    if zone["queue_count"] > max(3.0, target_people * 0.40):
        modifiers += min(20.0, zone["queue_count"] * 1.5)
    if zone["unauthorized"]:
        modifiers += min(30.0, 12.0 * len(zone["unauthorized"]))
    if zone["missing_dosimeter"] and ztype in {"RADIATION", "REACTOR", "CONTROLLED"}:
        modifiers += min(20.0, 8.0 * len(zone["missing_dosimeter"]))

    weighted_score = _clamp(base_score * rule["weight"] + modifiers)

    if predicted_density >= limit_density:
        status = "OVER_LIMIT"
    elif predicted_density >= target_density:
        status = "HIGH"
    elif predicted_density >= target_density * 0.65:
        status = "NORMAL"
    else:
        status = "LOW"

    return {
        "zone_id": zone["id"],
        "zone_type": ztype,
        "zone_label": zone["label"],
        "current_count": round(current_count, 1),
        "predicted_count": round(projected_count, 1),
        "area_m2": round(area_m2, 1),
        "density": round(density, 3),
        "predicted_density": round(predicted_density, 3),
        "target_density": round(target_density, 3),
        "limit_density": round(limit_density, 3),
        "capacity_people": round(capacity_people, 1),
        "utilization": round(min(utilization, 9.99), 3),
        "queue_count": round(zone["queue_count"], 1),
        "unauthorized_count": len(zone["unauthorized"]),
        "missing_dosimeter_count": len(zone["missing_dosimeter"]),
        "blocked": zone["blocked"],
        "opposite_flow": zone["opposite_flow"],
        "score": round(weighted_score, 1),
        "status": status,
    }

def _score_from_details(details):
    if not details:
        return 0.0
    scores = [d["score"] for d in details]
    mean_score = sum(scores) / len(scores)
    top_scores = sorted(scores, reverse=True)[:3]
    top_mean = sum(top_scores) / len(top_scores)
    return round(_clamp(max(scores) * 0.45 + top_mean * 0.35 + mean_score * 0.20), 1)

def layer1_density(zones, context):
    details = sorted((_zone_numbers(z, context) for z in zones), key=lambda x: x["score"], reverse=True)
    total_area = sum(d["area_m2"] for d in details) or 1.0
    current_people = sum(d["current_count"] for d in details)
    predicted_people = sum(d["predicted_count"] for d in details)
    return {
        "score": _score_from_details(details),
        "zone_count": len(details),
        "current_people": round(current_people, 1),
        "predicted_people": round(predicted_people, 1),
        "avg_density": round(current_people / total_area, 3),
        "predicted_avg_density": round(predicted_people / total_area, 3),
        "peak_density": round(max((d["predicted_density"] for d in details), default=0.0), 3),
        "details": details,
    }

def layer2_access_control(zones, context):
    relevant_types = {"ACCESS_GATE", "CONTROLLED", "RADIATION", "REACTOR", "CONTROL_ROOM", "DECON"}
    details = []
    for zone in zones:
        if zone["zone_type"] not in relevant_types and not zone["unauthorized"]:
            continue
        d = _zone_numbers(zone, context)
        access_score = d["score"] * 0.55
        access_score += min(35.0, d["unauthorized_count"] * 14.0)
        access_score += min(20.0, d["missing_dosimeter_count"] * 8.0)
        access_score += min(25.0, d["queue_count"] * 1.2)
        d["score"] = round(_clamp(access_score), 1)
        details.append(d)
    details.sort(key=lambda x: x["score"], reverse=True)
    return {
        "score": _score_from_details(details),
        "controlled_zone_count": len(details),
        "unauthorized_total": sum(d["unauthorized_count"] for d in details),
        "missing_dosimeter_total": sum(d["missing_dosimeter_count"] for d in details),
        "gate_queue_total": round(sum(d["queue_count"] for d in details if d["zone_type"] in {"ACCESS_GATE", "DECON"}), 1),
        "details": details,
    }

def layer3_evacuation(zones, obj_triples, context):
    evac_types = {"EVACUATION_ROUTE", "EXIT", "STAIR", "DECON", "MUSTER", "CORRIDOR", "ACCESS_GATE"}
    details = []
    for zone in zones:
        if zone["zone_type"] in evac_types or _as_bool(zone["meta"].get("evacuation_route")):
            d = _zone_numbers(zone, context)
            evac_score = d["score"] * 0.75
            if d["blocked"]:
                evac_score += 20.0
            if d["opposite_flow"]:
                evac_score += 12.0
            if d["utilization"] > 0.85:
                evac_score += 12.0
            d["score"] = round(_clamp(evac_score), 1)
            details.append(d)

    behavior_events = []
    event_score = 0.0
    risky_relations = {"blocking", "blocked_by", "stopped", "standing", "opposite_direction", "counterflow", "gathering", "queued", "queueing", "converging"}
    for t in obj_triples:
        if not isinstance(t, dict):
            continue
        relation = str(t.get("relation", "")).strip().lower()
        if relation in risky_relations:
            severity = 18.0 if relation in {"blocking", "blocked_by", "stopped"} else 10.0
            event_score += severity
            behavior_events.append({
                "subject": t.get("subject"),
                "relation": relation,
                "object": t.get("object"),
                "severity": round(severity, 1),
            })

    base = _score_from_details(details)
    score = round(_clamp(base * 0.75 + min(event_score, 60.0) * 0.25), 1)
    details.sort(key=lambda x: x["score"], reverse=True)
    return {
        "score": score,
        "route_count": len(details),
        "blocked_route_count": sum(1 for d in details if d["blocked"]),
        "opposite_flow_count": sum(1 for d in details if d["opposite_flow"]),
        "behavior_event_count": len(behavior_events),
        "bottlenecks": details,
        "behavior_events": behavior_events[:20],
    }

def layer4_radiation_operation(zones, context):
    details = []
    alarm = context["alarm"]
    phase = context["phase"]
    phase_score = 0.0
    if alarm in {"ALERT", "SITE_AREA_EMERGENCY", "GENERAL_EMERGENCY", "EMERGENCY", "EVACUATION"}:
        phase_score += 45.0
    elif alarm not in {"", "NORMAL", "NONE"}:
        phase_score += 20.0
    if phase in {"OUTAGE", "REFUELING", "MAINTENANCE", "检修", "换料"}:
        phase_score += 18.0

    for zone in zones:
        meta = zone["meta"]
        ztype = zone["zone_type"]
        if ztype not in {"REACTOR", "RADIATION", "CONTROLLED", "DECON"} and not _as_bool(meta.get("contamination_alarm")):
            continue
        d = _zone_numbers(zone, context)
        ref = _pick_number(meta.get("dose_rate_reference_usv_h"), default=25.0)
        dose = _pick_number(meta.get("dose_rate_usv_h"), meta.get("dose_rate"), default=0.0) or 0.0
        dose_score = _clamp(dose / max(ref, 0.1) * 100.0)
        contamination = _as_bool(meta.get("contamination_alarm") or meta.get("contaminated"))
        rad_score = d["score"] * 0.35 + dose_score * 0.45 + (25.0 if contamination else 0.0)
        d.update({
            "dose_rate_usv_h": round(dose, 3),
            "dose_reference_usv_h": round(ref, 3),
            "contamination_alarm": contamination,
            "score": round(_clamp(rad_score), 1),
        })
        details.append(d)

    base = _score_from_details(details)
    score = round(_clamp(base * 0.70 + phase_score * 0.30), 1)
    details.sort(key=lambda x: x["score"], reverse=True)
    return {
        "score": score,
        "alarm_state": alarm or "NORMAL",
        "operation_phase": phase or "NORMAL",
        "shift_change": context["shift_change"],
        "radiation_zone_count": len(details),
        "details": details,
    }

def classify(index):
    if index < 25:
        return "SAFE"
    if index < 50:
        return "CAUTION"
    if index < 75:
        return "SLOW"
    return "STOP"

def advisory(level, l1, l2, l3, l4):
    base = {
        "SAFE": "预测期内人流密度处于正常受控范围，可按当前门禁和巡检节奏运行。",
        "CAUTION": "预测期内存在局部人流升高，建议关注门禁、通道和受控区人员变化。",
        "SLOW": "预测期内可能形成拥堵或受控区聚集，建议启动分批放行并加强现场引导。",
        "STOP": "预测期内人流风险较高，建议暂停向高风险区域放行，优先疏散通道和门禁瓶颈。",
    }[level]

    extras = []
    hot = (l1.get("details") or [])[:1]
    if hot:
        z = hot[0]
        extras.append(f"{z['zone_label']}({z['zone_id']})预测密度{z['predicted_density']}人/㎡")
    if l2.get("unauthorized_total"):
        extras.append(f"受控区发现{l2['unauthorized_total']}个未授权/异常准入记录")
    if l3.get("blocked_route_count"):
        extras.append(f"{l3['blocked_route_count']}条疏散相关路径存在阻塞")
    if l4.get("alarm_state") not in {"", "NORMAL", "NONE"}:
        extras.append(f"当前报警状态为{l4['alarm_state']}")
    if extras:
        base += " 重点：" + "；".join(extras[:4]) + "。"
    return base

def _normalize_weights(weights):
    if not weights:
        return DEFAULT_WEIGHTS
    try:
        vals = [float(x) for x in weights]
    except (TypeError, ValueError):
        return DEFAULT_WEIGHTS
    while len(vals) < 4:
        vals.append(DEFAULT_WEIGHTS[len(vals)])
    vals = vals[:4]
    total = sum(max(v, 0.0) for v in vals)
    if total <= 0:
        return DEFAULT_WEIGHTS
    return tuple(max(v, 0.0) / total for v in vals)

def analyze(sg, weights=DEFAULT_WEIGHTS):
    context = _scene_context(sg)
    zones = collect_zones(sg)
    mt = sg.get("object_map_triples", [])
    ot = sg.get("object_object_triples", [])
    if not zones and mt:
        # Keep empty or legacy road graphs from crashing; they simply score as no facility occupancy.
        zones = []

    l1r = layer1_density(zones, context)
    l2r = layer2_access_control(zones, context)
    l3r = layer3_evacuation(zones, ot, context)
    l4r = layer4_radiation_operation(zones, context)
    w = _normalize_weights(weights)
    density_index = round(_clamp(l1r["score"] * w[0] + l2r["score"] * w[1] + l3r["score"] * w[2] + l4r["score"] * w[3]), 1)
    lv = classify(density_index)
    return {
        "image_id": sg.get("image_id", "?"),
        "metric": "NFDI",
        "metric_name": "核工厂人流密度预测指数",
        "horizon_minutes": context["horizon_minutes"],
        "spi": density_index,  # Backward compatible with the original frontend/list API.
        "density_index": density_index,
        "predicted_density": l1r["predicted_avg_density"],
        "peak_density": l1r["peak_density"],
        "level": lv,
        "level_name": LEVEL_NAME[lv],
        "level_color": LEVEL_COLOR[lv],
        "level_bg": LEVEL_BG[lv],
        "advisory": advisory(lv, l1r, l2r, l3r, l4r),
        "layers": {
            "layer1_zone_density": l1r,
            "layer2_access_control": l2r,
            "layer3_evacuation_route": l3r,
            "layer4_radiation_operation": l4r,
        },
        "weights": {"layer1": w[0], "layer2": w[1], "layer3": w[2], "layer4": w[3]},
    }

# ─────────────────────────────────────────────────────────────────────────────
# Folder Watcher — scans three directories and matches files by stem ID
# ─────────────────────────────────────────────────────────────────────────────

IMG_EXT  = {".jpg",".jpeg",".png",".webp",".bmp"}
BEV_EXT  = {".jpg",".jpeg",".png",".webp",".bmp"}
JSON_EXT = {".json"}

def extract_id(stem: str) -> str:
    """Extract numeric/alphanumeric ID from filename stem.
    000000_intersection -> 000000
    000000_scene_graph  -> 000000
    000000              -> 000000
    frame_001           -> 001
    """
    # Remove common suffixes
    for suffix in ["_scene_graph","_intersection","_bev","_seg","_map"]:
        stem = stem.replace(suffix, "")
    # Return cleaned stem
    return stem.strip("_- ")

class FolderWatcher:
    def __init__(self, images_dir, bev_dir, graphs_dir, interval=2.0):
        self.dirs = {
            "images": Path(images_dir) if images_dir else None,
            "bev":    Path(bev_dir)    if bev_dir    else None,
            "graphs": Path(graphs_dir) if graphs_dir else None,
        }
        self.interval = interval
        self._lock  = threading.Lock()
        self._cache = {}        # id -> {image, bev, graph, result, mtime}
        self._mtimes = {}       # path -> mtime
        self._running = False
        self._thread  = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("[Watcher] Started, scanning every", self.interval, "s")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self._scan()
            except Exception as e:
                print("[Watcher] Scan error:", e)
            time.sleep(self.interval)

    def _scan(self):
        # Collect all files per category
        images = {}; bevs = {}; graphs = {}

        def collect(d, ext_set, dest):
            if d and d.exists():
                for f in d.iterdir():
                    if f.suffix.lower() in ext_set:
                        fid = extract_id(f.stem)
                        dest[fid] = f

        collect(self.dirs["images"], IMG_EXT,  images)
        collect(self.dirs["bev"],    BEV_EXT,  bevs)
        collect(self.dirs["graphs"], JSON_EXT, graphs)

        all_ids = set(images) | set(bevs) | set(graphs)

        with self._lock:
            for fid in all_ids:
                img_path   = images.get(fid)
                bev_path   = bevs.get(fid)
                graph_path = graphs.get(fid)

                # Check if anything changed
                changed = False
                for p in [img_path, bev_path, graph_path]:
                    if p is None: continue
                    mt = p.stat().st_mtime
                    if self._mtimes.get(str(p)) != mt:
                        self._mtimes[str(p)] = mt
                        changed = True

                if fid not in self._cache or changed:
                    entry = {
                        "id": fid,
                        "image_path": str(img_path)   if img_path   else None,
                        "bev_path":   str(bev_path)   if bev_path   else None,
                        "graph_path": str(graph_path) if graph_path else None,
                        "result": None,
                        "error":  None,
                        "ts": time.time(),
                    }
                    # Run analysis if graph exists
                    if graph_path:
                        try:
                            with open(graph_path, encoding="utf-8") as f:
                                sg = json.load(f)
                            if "image_id" not in sg:
                                sg["image_id"] = fid
                            entry["result"] = analyze(sg)
                        except Exception as e:
                            entry["error"] = str(e)
                    self._cache[fid] = entry
                    if changed:
                        print(f"[Watcher] Updated: {fid}")

            # Remove stale entries no longer in any folder
            stale = [k for k in self._cache if k not in all_ids]
            for k in stale:
                del self._cache[k]
                print(f"[Watcher] Removed: {k}")

    def get_all(self):
        with self._lock:
            return sorted(self._cache.values(), key=lambda x: x["id"])

    def get_one(self, fid):
        with self._lock:
            return self._cache.get(fid)

    def set_dirs(self, images_dir=None, bev_dir=None, graphs_dir=None):
        with self._lock:
            if images_dir is not None: self.dirs["images"] = Path(images_dir) if images_dir else None
            if bev_dir    is not None: self.dirs["bev"]    = Path(bev_dir)    if bev_dir    else None
            if graphs_dir is not None: self.dirs["graphs"] = Path(graphs_dir) if graphs_dir else None
            self._cache.clear(); self._mtimes.clear()
        print("[Watcher] Dirs updated, cache cleared")

    def status(self):
        with self._lock:
            return {
                "images_dir": str(self.dirs["images"]) if self.dirs["images"] else None,
                "bev_dir":    str(self.dirs["bev"])    if self.dirs["bev"]    else None,
                "graphs_dir": str(self.dirs["graphs"]) if self.dirs["graphs"] else None,
                "total_ids":  len(self._cache),
                "analyzed":   sum(1 for v in self._cache.values() if v["result"]),
                "errors":     sum(1 for v in self._cache.values() if v["error"]),
            }

# ─────────────────────────────────────────────────────────────────────────────
# HTTP Handler
# ─────────────────────────────────────────────────────────────────────────────

watcher: FolderWatcher = None  # set in main

def image_to_data_url(path):
    if not path or not Path(path).exists():
        return None
    mt, _ = mimetypes.guess_type(path)
    mt = mt or "image/jpeg"
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return f"data:{mt};base64,{data}"

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{self.address_string()}] {fmt%args}")

    def cors(self):
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Headers","Content-Type")
        self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(body)))
        self.cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204); self.cors(); self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        qs     = parse_qs(parsed.query)

        if path == "/health":
            self.send_json({"status":"ok","version":"2.0"})

        elif path == "/status":
            self.send_json(watcher.status())

        elif path == "/list":
            items = []
            for entry in watcher.get_all():
                r = entry.get("result")
                items.append({
                    "id":       entry["id"],
                    "has_image": entry["image_path"] is not None,
                    "has_bev":   entry["bev_path"]   is not None,
                    "has_graph": entry["graph_path"]  is not None,
                    "spi":       r["spi"]   if r else None,
                    "density_index": r["density_index"] if r else None,
                    "metric":    r["metric"] if r else None,
                    "level":     r["level"] if r else None,
                    "level_color": r["level_color"] if r else None,
                    "error":     entry.get("error"),
                    "ts":        entry["ts"],
                })
            self.send_json({"items": items, "total": len(items)})

        elif path == "/detail":
            fid = qs.get("id", [None])[0]
            if not fid:
                self.send_json({"error":"Missing ?id="}, 400); return
            entry = watcher.get_one(fid)
            if not entry:
                self.send_json({"error":f"ID '{fid}' not found"}, 404); return
            # Include images as data URLs (inline base64)
            include_img = qs.get("img",["1"])[0] == "1"
            resp = dict(entry)
            if include_img:
                resp["image_data"] = image_to_data_url(entry["image_path"])
                resp["bev_data"]   = image_to_data_url(entry["bev_path"])
            # Don't leak full paths to frontend, just show filenames
            resp["image_file"] = Path(entry["image_path"]).name if entry["image_path"] else None
            resp["bev_file"]   = Path(entry["bev_path"]).name   if entry["bev_path"]   else None
            resp["graph_file"] = Path(entry["graph_path"]).name if entry["graph_path"] else None
            self.send_json(resp)

        else:
            self.send_json({"error":"Not found","endpoints":[
                "GET  /health", "GET  /status",
                "GET  /list", "GET  /detail?id=<id>&img=1",
                "POST /config", "POST /analyze",
            ]}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length",0))
        body   = json.loads(self.rfile.read(length).decode()) if length else {}
        path   = urlparse(self.path).path

        if path == "/config":
            # Update folder paths at runtime
            watcher.set_dirs(
                images_dir = body.get("images_dir"),
                bev_dir    = body.get("bev_dir"),
                graphs_dir = body.get("graphs_dir"),
            )
            self.send_json({"ok": True, "status": watcher.status()})

        elif path == "/analyze":
            # One-off analysis (no file needed, pass scene_graph directly)
            sg = body.get("scene_graph")
            if not sg:
                self.send_json({"error":"Missing scene_graph"},400); return
            w = body.get("weights", DEFAULT_WEIGHTS)
            self.send_json(analyze(sg, tuple(w)))

        else:
            self.send_json({"error":"Not found"}, 404)

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Nuclear Facility Pedestrian Density Prediction Server v2")
    ap.add_argument("--images",  default=None, help="Path to raw images folder")
    ap.add_argument("--bev",     default=None, help="Path to BEV images folder")
    ap.add_argument("--graphs",  default=None, help="Path to scene graph JSON folder")
    ap.add_argument("--port",    default=8765, type=int)
    ap.add_argument("--interval",default=2.0,  type=float, help="Folder scan interval (seconds)")
    args = ap.parse_args()

    watcher = FolderWatcher(args.images, args.bev, args.graphs, args.interval)
    watcher.start()

    # Do one immediate scan before accepting requests
    time.sleep(0.5)

    server = HTTPServer(("0.0.0.0", args.port), Handler)
    print(f"\n Nuclear Facility Density Engine v2  →  http://localhost:{args.port}")
    print(f"   Images dir : {args.images or '(not set)'}")
    print(f"   BEV dir    : {args.bev    or '(not set)'}")
    print(f"   Graphs dir : {args.graphs or '(not set)'}")
    print(f"   Scan every : {args.interval}s\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        watcher.stop()
