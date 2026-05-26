# 核工厂行人流密度预测系统

本项目用于对核工厂场景中的行人流密度进行实时预测和风险分级。后端监听原图、BEV 图和 Scene Graph JSON 三类文件，按同名 ID 自动匹配并计算 NFDI（Nuclear Facility Density Index，核工厂人流密度预测指数）；前端提供文件夹配置、场景列表、图像查看和分层风险详情。

> 说明：当前模型是面向科研展示和工程原型的规则模型，不等同于核设施正式合规判定。区域面积、密度阈值、门禁策略、剂量率参考值等参数需要结合真实厂区、规程和专家标定后使用。

## 文件结构

```text
.
├── server_v2.py      # Python 后端服务，无第三方依赖
├── index_v2.html     # 前端单页页面
└── README.md         # 项目说明
```

## 启动方法

1. 启动后端：

```bash
python server_v2.py --images ./images --bev ./bev --graphs ./graphs --port 8765
```

2. 打开前端：

直接用浏览器打开 `index_v2.html`，服务器地址保持默认 `http://localhost:8765`。

3. 也可以先启动后端，再在页面左侧填写三个文件夹路径并点击“应用路径”。

## 文件匹配规则

系统按文件名 stem 提取同一个场景 ID：

```text
images/000001.jpg
bev/000001_intersection.png
graphs/000001_scene_graph.json
```

以上会被匹配为场景 `000001`。后端会自动去掉 `_scene_graph`、`_intersection`、`_bev`、`_seg`、`_map` 等后缀。

## NFDI 评分逻辑

总分范围为 `0-100`，数值越高代表预测期内人流风险越高。默认预测窗口为 `5` 分钟。

四层评分权重：

```text
L1 区域密度：35%
L2 门禁受控：25%
L3 疏散通道：25%
L4 辐射运行：15%
```

风险等级：

```text
0-25   NORMAL / 正常受控
25-50  CAUTION / 重点关注
50-75  SLOW / 分批限流
75-100 STOP / 暂停进入
```

模型重点考虑核工厂常见约束：受控区和辐射区人员聚集应更敏感；门禁、去污/监测点和疏散通道出现排队、阻塞、逆向流时风险上升；检修、换料、交接班、应急报警等状态会修正预测人数和评分。

## Scene Graph JSON 示例

```json
{
  "image_id": "000001",
  "scene_meta": {
    "horizon_minutes": 5,
    "operation_phase": "OUTAGE",
    "alarm_state": "NORMAL",
    "shift_change": true
  },
  "zones": [
    {
      "id": "gate_A",
      "zone_type": "ACCESS_GATE",
      "label": "A区门禁",
      "area_m2": 12,
      "current_count": 8,
      "incoming_rate_ppm": 1.2,
      "outgoing_rate_ppm": 0.5,
      "queue_count": 6
    },
    {
      "id": "rad_corridor_1",
      "zone_type": "RADIATION",
      "label": "辐射控制区通道1",
      "area_m2": 28,
      "current_count": 9,
      "dose_rate_usv_h": 12,
      "dose_rate_reference_usv_h": 25
    }
  ],
  "object_map_triples": [
    {
      "subject": "worker_01",
      "subject_type": "WORKER",
      "object": "rad_corridor_1",
      "object_type": "RADIATION",
      "state": "inside",
      "subject_meta": {
        "authorized": true,
        "has_dosimeter": true
      }
    }
  ],
  "object_object_triples": [
    {
      "subject": "worker_02",
      "relation": "blocking",
      "object": "exit_1"
    }
  ]
}
```

支持的区域类型包括：`REACTOR`、`RADIATION`、`CONTROLLED`、`ACCESS_GATE`、`DECON`、`EVACUATION_ROUTE`、`EXIT`、`STAIR`、`MUSTER`、`CORRIDOR`、`CONTROL_ROOM`、`GENERAL`。如果 JSON 没有 `zones`，系统会尝试从 `object_map_triples` 的 `object_type`、`object_meta.zone_type`、区域名称中推断。

## API

```text
GET  /health
GET  /status
GET  /list
GET  /detail?id=<scene_id>&img=1
POST /config
POST /analyze
```

单次分析示例（PowerShell）：

```powershell
$body = @{
  scene_graph = @{
    image_id = "demo"
    zones = @(
      @{
        id = "gate_A"
        zone_type = "ACCESS_GATE"
        area_m2 = 10
        current_count = 12
        queue_count = 8
      }
    )
  }
} | ConvertTo-Json -Depth 8

Invoke-RestMethod -Uri http://localhost:8765/analyze -Method Post -ContentType "application/json" -Body $body
```

## 参考依据

模型设计参考了核设施应急准备、响应、设计安全和辐射防护中的公开原则，例如 IAEA GSR Part 7 对核与放射应急准备响应的要求、IAEA GS-G-2.1 对应急准备安排的指南，以及 IAEA SSR-2/1 对核电厂设计安全和纵深防御的要求。项目中的默认阈值是工程原型参数，不是法规限值。

参考链接：

- IAEA GSR Part 7: https://www.iaea.org/publications/10905/preparedness-and-response-for-a-nuclear-or-radiological-emergency
- IAEA GS-G-2.1: https://www.iaea.org/publications/7503/arrangements-for-preparedness-for-a-nuclear-or-radiological-emergency
- IAEA SSR-2/1 Rev.1: https://www.iaea.org/publications/10885/safety-of-nuclear-power-plants-design
