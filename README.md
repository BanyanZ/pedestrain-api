# Pedestrian API

[English](#english) | [简体中文](#简体中文)

---

<h2 id="english">English</h2>

A lightweight, real-time Pedestrian Slow-Pass Flow Analysis Server using pure Python.

The system watches three folders (`images`, `bev`, and `graphs`), matches files by stem ID, calculates intersection safety metrics through a three-layer engine, and serves analysis results via a REST API.

## 🚀 Quick Start

### 1. Requirements

This project relies **entirely on the Python Standard Library**. No external `pip` installations are required.

Tested on Python 3.8+.

### 2. Running the Server

Start the API server by passing directories to monitor:

```bash
python server_v2.py --images ./images --bev ./bev --graphs ./graphs --port 8765
```

**Options:**
- `--images`: Path to raw images folder.
- `--bev`: Path to BEV (Bird's Eye View) images folder.
- `--graphs`: Path to Scene Graph JSON folder.
- `--port`: Port to run the server on (default: `8765`).
- `--interval`: Scan interval in seconds (default: `2.0`).

### 3. Frontend Visualization

Open `index_v2.html` in your browser. It points to `http://localhost:8765` by default and will immediately visualize data as the server pulls it from the directories.

## 📡 API Documentation

### `GET /health`
Returns the operational status of the server.

**Response:** `200 OK`
```json
{
  "status": "ok",
  "version": "2.0"
}
```

### `GET /status`
Fetches directory states and analysis statistics.

**Response:** `200 OK`
```json
{
  "images_dir": "images",
  "bev_dir": "bev",
  "graphs_dir": "graphs",
  "total_ids": 10,
  "analyzed": 10,
  "errors": 0
}
```

### `GET /list`
List all frames currently matched and analyzed.

**Response:** `200 OK`
```json
{
  "items": [
    {
      "id": "000000",
      "has_image": true,
      "has_bev": true,
      "has_graph": true,
      "spi": 28.5,
      "level": "CAUTION",
      "level_color": "#0F6E56",
      "error": null,
      "ts": 1711612800.0
    }
  ],
  "total": 1
}
```

### `GET /detail?id=<id>`
Fetch detailed analysis for a specific frame ID. By default, inline base64 images are included. To exclude them, append `&img=0`.

**Request Query:**
- `id`: The frame ID (e.g., `000000`).
- `img` (optional): Defaults to `1`. Set to `0` to omit `image_data` and `bev_data` base64 fields, reducing payload size.

**Response:** `200 OK`
```json
{
  "id": "000000",
  "image_path": "images/000000.jpg",
  "bev_path": "bev/000000_intersection.png",
  "graph_path": "graphs/000000_scene_graph.json",
  "result": {
    "image_id": "000000",
    "spi": 28.5,
    "level": "CAUTION",
    "level_color": "#0F6E56",
    "level_bg": "#E1F5EE",
    "advisory": "行人请注意观察，路口存在少量车辆，建议确认车辆停稳后通过。 风险：车辆2(CAR)占据斑马线45%。",
    "layers": { ... },
    "weights": {
      "layer1": 0.5,
      "layer2": 0.3,
      "layer3": 0.2
    }
  },
  "error": null,
  "ts": 1711612800.0,
  "image_data": "data:image/jpeg;base64,...",
  "bev_data": "data:image/png;base64,...",
  "image_file": "000000.jpg",
  "bev_file": "000000_intersection.png",
  "graph_file": "000000_scene_graph.json"
}
```

### `POST /config`
Dynamically change the source folders at runtime.

**Request Body:**
```json
{
  "images_dir": "./new_images",
  "bev_dir": "./new_bev",
  "graphs_dir": "./new_graphs"
}
```
**Response:** `200 OK`
```json
{
  "ok": true,
  "status": { ... }
}
```

### `POST /analyze`
Perform a one-off analysis by submitting raw scene graph data. Use this if you want to bypass the folder-watching logic.

**Request Body:**
```json
{
    "scene_graph": { ... },
    "weights": [0.5, 0.3, 0.2]
}
```
*Note: `weights` maps to layer1, layer2, layer3 calculation importance.*

**Response:** `200 OK`
Returns the `"result"` object layout seen in `/detail` endpoint.

## 📊 Minimum `scene_graph` JSON Format

The inference engine relies on `scene_graph` structure formatted conceptually as:

```json
{
  "image_id": "000000",
  "object_map_triples": [
    {
      "subject": 1,
      "subject_type": "CAR",
      "relation": "crossing",
      "object": 10,
      "object_type": "CROSSWALK",
      "state": "inside",
      "inter_ratio": 0.65,
      "object_meta": {
        "is_intersection": true
      }
    }
  ],
  "object_object_triples": [
    {
      "subject": 2,
      "subject_type": "CAR",
      "relation": "following",
      "object": 1,
      "object_type": "CAR"
    }
  ]
}
```

## 🧠 Engine Weight Configuration

The SPI score (Safety Performance Index) aggregates across three layers.

When calculating `analyze()`, default weight configurations are `[0.5, 0.3, 0.2]`:

- **Layer 1 (Crosswalk Occupancy - Weight: 0.5):** Heavier penalties calculation using ratios weighted by vehicle footprints.
- **Layer 2 (Intersection Density - Weight: 0.3):** Counts vehicles occupying > 50% ratio in general intersection areas.
- **Layer 3 (Vehicle Behavior - Weight: 0.2):** Looks for following chains pushing towards already blocked crosswalks.

These weightings decide the final SPI class: `SAFE` (0-24), `CAUTION` (25-49), `SLOW` (50-74), `STOP` (75-100).

---

<h2 id="简体中文">简体中文</h2>

一个轻量级、完全基于 Python 编写的实时行人缓行流分析服务器。

系统通过监听三个文件夹（`images`、`bev` 和 `graphs`），根据文件名的标识符相互匹配文件，通过三层引擎计算路口安全指标，并提供 REST API 以获取实时分析结果。

## 🚀 快速开始

### 1. 运行环境

本项目**完全依赖于 Python 标准库**。无需通过 `pip` 安装任何外部包。

在 Python 3.8+ 测试通过。

### 2. 运行服务器

启动 API 服务器并传入需要监控的目录：

```bash
python server_v2.py --images ./images --bev ./bev --graphs ./graphs --port 8765
```

**选项:**
- `--images`: 原始图像文件夹路径。
- `--bev`: BEV（鸟瞰图）图像文件夹路径。
- `--graphs`: Scene Graph (场景图) JSON 文件夹路径。
- `--port`: 运行服务器的端口（默认：`8765`）。
- `--interval`: 扫描文件夹间隔时间，单位为秒（默认：`2.0`）。

### 3. 前端可视化

在浏览器中打开 `index_v2.html` 文件。默认它会连接到 `http://localhost:8765`，当服务器拉取并完成计算数据时会立即进行可视化展示。

## 📡 API 文档

### `GET /health`
返回服务器的运行状态。

**响应:** `200 OK`
```json
{
  "status": "ok",
  "version": "2.0"
}
```

### `GET /status`
获取受到监控的目录状态和分析统计数据。

**响应:** `200 OK`
```json
{
  "images_dir": "images",
  "bev_dir": "bev",
  "graphs_dir": "graphs",
  "total_ids": 10,
  "analyzed": 10,
  "errors": 0
}
```

### `GET /list`
列出当前已匹配并被分析的所有帧。

**响应:** `200 OK`
```json
{
  "items": [
    {
      "id": "000000",
      "has_image": true,
      "has_bev": true,
      "has_graph": true,
      "spi": 28.5,
      "level": "CAUTION",
      "level_color": "#0F6E56",
      "error": null,
      "ts": 1711612800.0
    }
  ],
  "total": 1
}
```

### `GET /detail?id=<id>`
获取特定帧 ID 的详细分析结果。默认情况下会包含内联的 base64 图像数据，如需排除图片请附加参数 `&img=0`。

**请求查询参数:**
- `id`: 帧 ID（例如：`000000`）。
- `img` (可选): 默认为 `1`。设置为 `0` 可在响应中排除 `image_data` 和 `bev_data` 字段，从而减小传输体积。

**响应:** `200 OK` (内容同上文英文文档示例)

### `POST /config`
在运行时动态更改数据监听来源文件夹。

**请求体:**
```json
{
  "images_dir": "./new_images",
  "bev_dir": "./new_bev",
  "graphs_dir": "./new_graphs"
}
```
**响应:** `200 OK`
```json
{
  "ok": true,
  "status": { ... }
}
```

### `POST /analyze`
通过直接提交原始场景图数据执行一次性分析请求，以跳过轮询文件夹监听逻辑。

**请求体:**
```json
{
    "scene_graph": { ... },
    "weights": [0.5, 0.3, 0.2]
}
```
*注：`weights` 分别映射为 layer1、layer2、layer3 的计算重要性权重。*

**响应:** `200 OK` 返回与 `/detail` 一致的 `"result"` 字段数据。

## 📊 Minimum `scene_graph` JSON 极简格式

推断引擎依赖于一种特定的 `scene_graph` 模型结构，逻辑上体现如下：

```json
{
  "image_id": "000000",
  "object_map_triples": [
    {
      "subject": 1,
      "subject_type": "CAR",
      "relation": "crossing",
      "object": 10,
      "object_type": "CROSSWALK",
      "state": "inside",
      "inter_ratio": 0.65,
      "object_meta": {
        "is_intersection": true
      }
    }
  ],
  "object_object_triples": [
    {
      "subject": 2,
      "subject_type": "CAR",
      "relation": "following",
      "object": 1,
      "object_type": "CAR"
    }
  ]
}
```

## 🧠 引擎权重配置 (Engine Weight Configuration)

SPI 分数（安全性能指数，Safety Performance Index）通过三个独立层计算并聚合得出。

在 `analyze()` 中，默认的权重配置为 `[0.5, 0.3, 0.2]`：

- **Layer 1 (斑马线占用度 - 权重: 0.5)：** 采用较高的惩罚机制，根据占用的重叠比例与车辆类型（不同车辆有不同的基础权重）相乘得出安全度指标。
- **Layer 2 (路口拥堵度 - 权重: 0.3)：** 计算进入总路口范围内（占比大于 50%）的车辆总数来评估密度。
- **Layer 3 (车辆行为分析 - 权重: 0.2)：** 探测跟车状况。若存在跟随且驶向已拥挤区域内的车流会触发加分风险惩罚。

最终这些权重决定了 SPI 安全等级的划分界限：`SAFE/安全` (0-24), `CAUTION/注意` (25-49), `SLOW/缓行` (50-74), `STOP/停止` (75-100)。

## ⚖️ License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details. / 本项目基于 MIT 协议开源 - 详情请查看 [LICENSE](LICENSE) 文件。
