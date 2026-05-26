# 行人流密度预测系统

本项目用于对图像、BEV 鸟瞰图和 Scene Graph JSON 进行匹配分析，预测短时间窗口内的行人流密度变化，并给出拥挤风险等级。系统由一个 Python 后端服务和一个前端单页页面组成，适合用于科研展示、实验验证和原型系统演示。

> 说明：当前模型是面向科研和工程原型的规则模型，输出结果用于辅助分析，不等同于真实场景中的安全管理或调度决策。区域面积、密度阈值、通行能力和风险权重应结合具体数据集与场景重新标定。

## 文件结构

```text
.
├── server_v2.py      # Python 后端服务，无第三方依赖
├── index_v2.html     # 前端单页页面
└── README.md         # 项目说明
```

## 启动方法

1. 启动后端服务：

```bash
python server_v2.py --images ./images --bev ./bev --graphs ./graphs --port 8765
```

2. 打开前端页面：

直接用浏览器打开 `index_v2.html`，服务器地址保持默认 `http://localhost:8765`。

3. 也可以先启动后端，再在页面左侧填写三个文件夹路径并点击“应用路径”。

## 输入数据

系统默认读取三类文件：

- 原始图像：监控画面、街景图或数据集原图。
- BEV 图：鸟瞰图、平面映射图或轨迹投影图。
- Scene Graph JSON：场景中的区域、行人、关系和统计字段。

三类文件通过文件名 stem 自动匹配为同一个场景。例如：

```text
images/000001.jpg
bev/000001_intersection.png
graphs/000001_scene_graph.json
```

以上文件会被匹配为场景 `000001`。后端会自动去掉 `_scene_graph`、`_intersection`、`_bev`、`_seg`、`_map` 等常见后缀。

## 评分逻辑

系统输出 `density_index`，范围为 `0-100`，数值越高表示预测窗口内的行人密度和拥挤风险越高。默认预测窗口为 `5` 分钟。

当前分析由四类信息组成：

```text
L1 区域密度：根据当前人数、预测人数、区域面积和密度阈值评分
L2 出入口/受控点：根据排队人数、异常进入记录和局部拥堵评分
L3 通道与疏散路径：根据通道利用率、阻塞、逆向流和聚集行为评分
L4 场景状态修正：根据运行阶段、告警状态或特殊区域配置修正风险
```

风险等级：

```text
0-25   SAFE     正常
25-50  CAUTION  关注
50-75  SLOW     限流
75-100 STOP     暂停进入
```

## Scene Graph JSON 示例

```json
{
  "image_id": "000001",
  "scene_meta": {
    "horizon_minutes": 5,
    "operation_phase": "NORMAL",
    "alarm_state": "NORMAL",
    "shift_change": false
  },
  "zones": [
    {
      "id": "gate_A",
      "zone_type": "ACCESS_GATE",
      "label": "入口 A",
      "area_m2": 12,
      "current_count": 8,
      "incoming_rate_ppm": 1.2,
      "outgoing_rate_ppm": 0.5,
      "queue_count": 6
    },
    {
      "id": "corridor_1",
      "zone_type": "CORRIDOR",
      "label": "主通道 1",
      "area_m2": 28,
      "current_count": 14,
      "incoming_rate_ppm": 1.5,
      "outgoing_rate_ppm": 0.8,
      "opposite_flow": true
    },
    {
      "id": "exit_1",
      "zone_type": "EXIT",
      "label": "出口 1",
      "area_m2": 16,
      "current_count": 10,
      "blocked": false
    }
  ],
  "object_map_triples": [
    {
      "subject": "person_01",
      "subject_type": "PEDESTRIAN",
      "object": "corridor_1",
      "object_type": "CORRIDOR",
      "state": "inside"
    }
  ],
  "object_object_triples": [
    {
      "subject": "person_02",
      "relation": "queued",
      "object": "gate_A"
    }
  ]
}
```

常用区域类型包括：

```text
ACCESS_GATE        出入口/闸机
EVACUATION_ROUTE   疏散路径
EXIT               出口
STAIR              楼梯
MUSTER             集合点
CORRIDOR           通道
GENERAL            普通区域
```

如果 JSON 没有 `zones`，系统会尝试从 `object_map_triples` 中的 `object_type`、`object_meta.zone_type` 或区域名称推断区域类型。

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
        label = "入口 A"
        area_m2 = 10
        current_count = 12
        queue_count = 8
      },
      @{
        id = "corridor_1"
        zone_type = "CORRIDOR"
        label = "主通道"
        area_m2 = 30
        current_count = 18
        incoming_rate_ppm = 1.5
        outgoing_rate_ppm = 0.6
      }
    )
  }
} | ConvertTo-Json -Depth 8

Invoke-RestMethod -Uri http://localhost:8765/analyze -Method Post -ContentType "application/json" -Body $body
```

## 输出字段

`/analyze` 和 `/detail` 会返回分析结果，主要字段包括：

- `density_index`：行人流密度预测指数，范围 `0-100`。
- `predicted_density`：预测平均密度。
- `peak_density`：预测峰值密度。
- `level`：风险等级，取值为 `SAFE`、`CAUTION`、`SLOW`、`STOP`。
- `advisory`：系统生成的简要处置建议。
- `layers`：四类子评分及明细。

## 适用场景

本项目可用于校园、车站、商场、园区、展馆、街区等公共空间的行人流预测实验，也可以作为 Scene Graph 与 BEV 表征在人群密度分析任务中的可视化原型。
