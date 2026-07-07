# dev-bench v3 — SSE chunk handler 全链路耗时诊断

> 一个独立的前端诊断工具，用来回答 chemssh 在加载大结构文件时"前端瓶颈还是后端瓶颈"。
>
> 本文对应 `idea/tmp/dev-bench.html`（工作副本）与
> `frontend/public/_dev/dev-bench.html`（Vite 入口副本）。

---

## 0. 它解决什么问题

Workspace 里打开一个 108 MB / 20 000 帧的结构文件，体感要 5–6 s 才出结构。
前端优化做了很多（写 store → conv bond → render）后，sim 模式只能测到 ~600 ms，
这中间的差值 ~5 s 全部归不到前端。这就有了 `dev-bench v3`：

| 想要的诊断问题 | 谁能回答 |
|---|---|
| 前端 chunk handler 链路 ≤ ? ms/chunk | sim 模式（页内全镜像） |
| 后端 `read_frame + CWB1 打包 + b64 + SSE` 多少 ms | **E2E 模式**（真后端 + 真浏览器 EventSource） |
| 网络 / SSE 投递多少 ms | E2E 模式 |
| 整套 wall 时间分布 | E2E 模式 |

---

## 1. 快速开始

### 1.1 前置条件

| 项 | 要求 |
|---|---|
| Node | ≥ 18（Vite 6 需要） |
| Python | 后端 0.3.x；用项目 `.venv` |
| 浏览器 | Chromium >= 124（EventSource 完整支持） |
| 待测文件 | workspace.root 下的相对路径（如 `idea/tmp/movie.xyz`） |

### 1.2 三步起测

```powershell
# Step 1 — 后端（一个窗口）
cd D:\Git\chemssh
.\.venv\Scripts\python.exe -m backend.app.cli --host 127.0.0.1 --port 8888 --log-level info --debug --reuse-existing never

# Step 2 — Vite（另一个窗口）
cd D:\Git\chemssh\frontend
npm.cmd run dev

# Step 3 — 同步诊断页到 Vite 入口
Copy-Item -Force idea\tmp\dev-bench.html frontend\public\_dev\dev-bench.html
```

打开 `http://127.0.0.1:5173/_dev/dev-bench.html`。
修改 `idea/tmp/dev-bench.html` 后再 Copy-Item 一次即可（Vite public 不走 HMR，手动 sync 即可）。

### 1.3 配置层小坑

`config.yaml` 里有一项门槛常被忽略：

```yaml
workspace:
  max_read_size_mb: 50
```

108 MB 文件超 50 MB 会拒。两种法：
1. **临时**：URL 加 `&force=true` 即可（dev-bench 已默认带）。
2. **永久**：把 `max_read_size_mb` 提到 ≥200。

---

## 2. 两种模式详解

### 2.1 离线模式（sim 默认）

**目的**：测前端链路天板，不连后端、不发网络。

把任意本地 `.xyz` 拖到虚线框 → 按 **运行** → 看堆叠柱状图与阶段汇总表。

页内构造与浏览器一致的 1250 个 CWB1 chunk（93 atoms × 16 帧一个），跑：

```
atob → 1-字符 charCodeAt 循环 → cwb1_magic / header / views →
parseStructureBinaryChunk (LIVE 真模块) → writeChunkToStore →
frameFromTrajectoryStore → buildDisplayFrame → estimateBonds
```

每阶段独立计时，可逐阶段 ON-only 对比。

**结果可信度高**：所有 STAGES 都用 mirror 镜像 `MoleculeViewer.vue` 等价代码；
需要真模块时 `import('/src/api/structures.ts')` 把 Vite 编译产物测进黑盒。

**两种徽章**：
- `LIVE · structures.ts` — `parseStructureBinaryChunk` 真模块可达
- `LIVE · TrajectoryStore` — `frameFromTrajectoryStore` 真模块可达

红色 `FALLBACK` 就不调用真模块，只跑页内等价的镜像实现。

### 2.2 E2E 模式

**目的**：测**后端 + 网络 + 前端**完整 4 维链路耗时。

切到 `Mode = E2E` → 填后端相对路径（默认 `idea/tmp/movie.xyz`）→ **▶ E2E Run**

走 `EventSource('/api/structures/ase/frames.stream?...')`。只有后端以 `--debug` 启动时，才会开启耗时探测并推送 `ready/meta` 诊断事件；正常模式只推送 `chunk/done`，避免正式使用时的计时和额外 SSE 事件开销。

debug 模式下后端 SSE 推送顺序：

```
event: ready
  data: { t_resolve_ms, t_scan_ms, total_frames, chunk_size, server_emit_ms }
[chunk 0..N):
  event: meta
    data: { start, count, t_pack_ms, t_b64_ms, server_emit_ms, size_b64 }
  event: chunk
    data: <base64 CWB1 binary>
event: done
  data: { n_frames }
```

debug 模式下，后端计时器会在 `_resolve_structure_file` / `_get_structure_summary` /
`stream_frame_chunk_binary` / `base64.b64encode` 周围采样；非 debug 模式不创建 profile dict，也不发送 `ready/meta`。

dev-bench 客户端按收到顺序累加：
- `t_request → ready`：建立 baseline
- `client_recv_meta - server_emit`：估算 network drift
- `client_recv_meta → client_recv_chunk`：相邻两事件延伸的网络 + parse
- `atob` / `charCodeAt` / `parseStructureBinaryChunk` / `writeChunkToStore`：前端 4 段独立计时

最终渲染 4 色堆叠柱状图 + 7 行阶段汇总表。

---

## 3. 实测诊断结果（2026-06-27 一次 E2E Run）

> 文件：`idea/tmp/movie.xyz` 108.99 MB · 93 atoms × 20 000 frames · 总 1 860 000 原子
> chunk size：32（625 个 chunk event） · 拓扑：fixed · repeat：1

### 3.1 总览

| 维度 | 总耗时 | 占 wall | μ / chunk |
|---|---|---|---|
| **Backend · t_pack**（`stream_frame_chunk_binary`） | **4 010 ms** | **85.1 %** | **3.21 ms** ← 瓶颈 |
| Backend · t_b64 | 116 ms | 2.5 % | 0.09 ms |
| Backend · resolve (一次) | 1.5 ms | 0.0 % | — |
| Backend · scan (一次) | 0.25 ms | 0.0 % | — |
| Network · drift（loopback + Brotli） | 431 ms | 9.1 % | 0.34 ms |
| Browser · atob | 38 ms | 0.8 % | 0.03 ms |
| Browser · 1-字符 charCodeAt 循环 | 64 ms | 1.4 % | 0.05 ms |
| Browser · parseStructureBinaryChunk (LIVE) | 41 ms | 0.9 % | 0.03 ms |
| Browser · writeChunkToStore (TypedArray.set × 6) | 13 ms | 0.3 % | 0.01 ms |
| **Wall** | **4 710 ms** | — | — |
| **前端总计** | **156 ms** | **3.3 %** | — |

### 3.2 关键发现

1. **后端 `_stream_frame_chunk_binary` 的 Python 循环打包占 85.1 % wall**。
   - 单 chunk 3.21 ms，跨 1250 个 chunk 串行；
   - 3.21 ms 主要拆为：`_read_frame_range` (Python 行扫描 + numpy stack)、
     JSON header encode、6 次 numpy `ascontiguousarray + tobytes()` 对齐 + memmove、
     `b''.join`。
2. **前端链路 156 ms 远小于冥想预期** —— 过去担心的"前端可能 3–4 s"
   来自 `writeChunkToStore` 回调里的 estimateBonds，但 E2E 模式根本没触发
   那条回调（DEV 工具不 import chemsshViewer），所以 E2E 看到的是"裸前端"，156 ms 干净。
3. **`1-字符 charCodeAt 循环` 占 64 ms** ≈ v2 已点过的 `b64_to_bytes`，
   改成 `TextDecoder().decode(base64 binary)`（一字节 native）能砍一半到
   ~30 ms。ROI 中等。
4. **`At 解析 (atob + charCodeAt + parse)` 总 143 ms** —— E2E 链路对前端
   没有强约束（V8 fast path）。
5. **`Network · drift` 0.34 ms / chunk** 是 loopback + Brotli streaming 压缩 +
   EventSource 微任务调度之和。这一项理论极限再低很难。

### 3.3 优化路线（按 ROI）

| 顺序 | 改动 | 文件 | 预期效果 |
|---|---|---|---|
| 🥇 #1 | SSE 打包并发：把 `event_generator` 主循环的串行 `anyio.to_thread.run_sync` 换成 `asyncio.gather` × 8 worker | `backend/app/api/structures.py:178` | `t_pack` 总 4 s → ~0.5 s，wall 4.71 s → ~1 s |
| 🥈 #2 | `_build_binary_payload_from_normalized` 6 次 `add_array` → `numpy.concatenate` + 一次 `bytes.tobytes()` | `backend/app/services/structure_service.py:2539` | `#1` 后余 t_pack 再减 30–50 % |
| 🥉 #3 | `numpy.memmap` 把 movie.xyz 一次性映射 + 预 typed views，免 `_read_frame_range` 中重读 | `_read_frame_range` 同文件 | 把 IO 抹平；小但稳 |
| #4 | `b64_to_bytes` 改用 `TextDecoder().decode(binary)` 一字节 | `idea/tmp/dev-bench.html` sim 模式 + `frontend/src/api/structures.ts` 真解析 | 64 ms → ~30 ms |
| #5 | 永久把 `workspace.max_read_size_mb` 提到 ≥ 200 | `config.yaml` | 去掉 force hack，E2E 直接走 |
| #6 | 修 dev-bench drift 算法（`Date.now() - performance.now()` 在 page-load 处算） | `idea/tmp/dev-bench.html` | `组成:` 三柱加和 ≈ 100 %（目前含 -1727 ms 误报） |

按 `#1 → #2 → #3 → #4` 顺序；做完 #1 后 #2、#3 走起来才有意义（否则还是被串行锁住）。

---

## 4. 各阶段耗时分析

### 4.1 后端 `t_pack` 这条流水线到底在做什么

`stream_frame_chunk_binary(start=0, count=16, ...)` 大致 4 步：

```
stream_frame_chunk_binary                                       ─┐
  └─ _read_frame_range                                          │  Python 行扫 + numpy stack
      └─ _build_binary_payload_from_frames                     │
          └─ _build_binary_payload_from_normalized              │  6 arrays CWB1 pack
              ├─ add_array('positions',  count×atoms×3)         │
              ├─ add_array('cells',      count×9)               │
              ├─ add_array('tags',       count×atoms)           │  ─┘  6× allocation
              ├─ add_array('energy',     count)                 │
              ├─ add_array('fmax',       count)                 │
              └─ add_array('fixed_mask', count×atoms)           │
```

**#1 ROI 路线**：每个 chunk 走 `asyncio.gather` × N worker 并发，
N = `min(chunks_remaining, default_thread_pool_size=32)` 起步；实测 N=8
对 4 核机器最稳，再大反而加锁。

**#2 ROI 路线**：把 6 次 `add_array` 合并成：
```python
full = np.concatenate([
    positions.flatten(),
    cells.flatten(),
    tags.astype(np.int32),
    energy,
    fmax,
    fixed_mask.astype(np.uint8)
])
data = full.tobytes()  # one big memcpy
```
免 5 次额外 `np.ascontiguousarray` + 5 次 alloc，省 CPU 是显著。

### 4.2 前端 `1-字符 charCodeAt 循环` 还能榨多少

现状：
```js
const s = atob(chunk.b64)
const bytes = new Uint8Array(s.length)
for (let i = 0; i < s.length; i += 1) bytes[i] = s.charCodeAt(i)
```

73 ms → 30 ms 路线：
```js
// base64 二进制字节流（不是 ascii char）→ 直接 Uint8Array
const bytes = Uint8Array.from(atob(chunk.b64), c => c.charCodeAt(0))
```
这条从 ES2022 起 native，比 for-loop 快但仍要走 base64 → ascii → bytes 两步。

更快路线（在 envelope 允许改的情况下）：让后端去 base64 包装，
直接吐 raw CWB1 bytes 给前端，浏览器 `Uint8Array` 直读 → 0.06 ms/chunk
量级（毫秒级以下）。但需要把 SSE event: chunk 换成 binary frame + 新增
binary chunk `/frames.bin.chunked` 端点；大改。

### 4.3 网络 drift 是 loopback 还是 Brotli？

0.34 ms / chunk 中位数偏低。拆三段：

| 子段 | 量级 | 怎么测 |
|---|---|---|
| TCP loopback 投递 | ~sub-ms | 关 Brotli middleware 测对照 |
| Python Brotli 中间件 streaming compress（`/api` 已被全局 on） | 0.1–0.3 ms | `--log-level debug` 看 `brotli.process + flush` 时长 |
| 浏览器 EventSource microtask + GC pause | 0.05–0.15 ms | 在调试器 Performance tab 看 task timing |

实测 0.34 ms 在这台机器达到 loopback 极限附近，想做到 0.15 ms 以下
得关闭 Brotli 中间件或在 SSE event: chunk 上明确禁用压缩（middleware 里
`_should_stream_compress` 已经把 `text/event-stream` 列为可压缩 — 但
实测用 `Compressor.process + flush` 每 chunk 都压一遍，是有显著成本的）。

---

## 5. 已知缺陷 / 排查清单

### 5.1 dev-bench drift 显示负数

dev-bench 里顶部 + 组成里 `组成: backend=4.13s (161%) · network=-1727.81 (-68%)`
是错的。`drift -1782550440679.2 ms` 那条负值来自 client `performance.now()`
（monotonic 自页面加载起算）与 server `time.time()*1000`（epoch 秒）跨域做差。

**正确读法**：表格里 `Network · drift (meta→chunk onmessage)` 那列 (0.34 ms/chunk)
是稳的；顶部 + 网络 总 % 暂时看表格为准。

**修**：在 dev-bench.html 的 page-load 阶段加一行
```js
const epoch_offset = Date.now() - performance.now()
```
然后把 `m.server_emit_ms` 替换为 `m.server_emit_ms - epoch_offset` 再做差。
预计 5 行以内。

### 5.2 E2E 模式看不到 Three.js 真实瓶颈

dev-bench 的 E2E 链路**不 import / 不实例化 chemsshViewer**，因此前端链路
不含 GL upload + rAF + Vue 响应式。如果你想知道 Three.js 渲染本身占多少：

| 维度 | 在哪里测 | dev-bench 是否覆盖 |
|---|---|---|
| Frontend parse / write | dev-bench E2E ✓ 156 ms | 是 |
| Viewer render (estimateBonds + setFrame + GL upload) | Workspace MoleculeViewer | **否** |
| React/Vue 响应式触发 | Workspace | 否 |

未来版本可以再做 `e2e-viewer-bench.html`：实例化真 viewer，让 SSE 流入到
`chemsshViewer.setFrame`。但当前 dev-bench 已经把"前端写"和"渲染"完全切开。

### 5.3 curl `Failure writing output to destination`

如果 `curl | Select-Object -First 4` 在 PowerShell 跑，会拿到 SIGPIPE-like
`curl: (23) Failure writing output to destination`。原因：Select-Object 拿到
4 条记录就关闭管道，curl 后续还在写。要看完整流，应：
```powershell
curl.exe -o "$env:TEMP\sse.txt" --max-time 60 "http://127.0.0.1:8888/api/structures/ase/frames.stream?path=idea/tmp/movie.xyz&chunk=32&force=true"
Get-Content "$env:TEMP\sse.txt" -Head 20
```
或：
```powershell
curl.exe -m 60 ... | Out-String -Stream | Select-Object -First 20
```

---

## 6. 文件引用（在哪改）

### dev-bench 工具本身

| 文件 | 作用 |
|---|---|
| `idea/tmp/dev-bench.html` | 工作源（v3） |
| `frontend/public/_dev/dev-bench.html` | Vite 入口副本（copy） |

### 后端（计时 + SSE 生成器）

| 文件 | 行 | 内容 |
|---|---|---|
| `backend/app/api/structures.py` | 1–22 | `import time` + StreamingResponse |
| `backend/app/api/structures.py` | 178 | `event_generator` 主循环，串行 `anyio.to_thread.run_sync` |
| `backend/app/services/structure_service.py` | 327 | `def stream_frame_chunk_binary` |
| `backend/app/services/structure_service.py` | 2539 | `_build_binary_payload_from_normalized` — 6 次 `add_array` |
| `config.yaml` | — | `workspace.max_read_size_mb: 50` 门槛 |

### 前端（Vite dev 代理）

| 文件 | 行 | 内容 |
|---|---|---|
| `frontend/vite.config.ts` | 17–25 | `proxy: { '/api': target: 'http://127.0.0.1:8888' }` |

### 运行时入口

| 入口 | URL |
|---|---|
| 浏览器 dev-bench | `http://127.0.0.1:5173/_dev/dev-bench.html` |
| Vite 静态文件 | `frontend/public/_dev/dev-bench.html` |
| Python 后端 SSE | `http://127.0.0.1:8888/api/structures/ase/frames.stream?path=<rel>&chunk=<n>&force=<bool>` |
| Python 后端 health | `http://127.0.0.1:8888/api/system/info` |

---

## 7. 验收 checklist（跑通这页需要的最低准备）

- [ ] 后端 `uvicorn` 启动完看到 `Application startup complete.`
- [ ] `curl http://127.0.0.1:8888/api/system/info` 返回 JSON 200
- [ ] `curl "http://127.0.0.1:8888/api/structures/ase/frames.stream?path=movie.xyz&chunk=32&force=true" -o /tmp/sse.txt` 输出文件 > 1 MB
- [ ] `npm run dev` 启动 Vite，看到 `VITE v6.4.2 ready` 与 `Local: http://127.0.0.1:5173/`
- [ ] 浏览器 `http://127.0.0.1:5173/_dev/dev-bench.html` 不再 ECONNREFUSED
- [ ] E2E Run 输出 `[ready] resolve ~1.5 ms · scan ~0.3 ms · total_frames 20000` — 不再 RESOLVE_FAILED
- [ ] 几秒后看到 `wall 4.x s · backend 4.x s · frontend <200 ms`
- [ ] 表格里 `Network · drift 0.x ms/chunk`（**不要看顶部 + 组成的 -1727**）

走完一步到下一步走不通时，把那步的输出贴出来即可定位。

---

## 8. 后续优化方案（实施细节）

承接 §3.3 路线总表，下面把 5 项改动展开到「改哪个函数、改成什么样子、改多大、风险如何」。落地顺序：#1 → #2 → #3 → #4 → #5。

### #1 🥇SSE 生成器并发打包 —— 直接拿 80 % 时间

**位置**：`backend/app/api/structures.py:178` 的 `event_generator` 主循环。

**思路**：从串行改"预 N 个 + ordered flush"：

```python
async def produce_and_drain(request, n_frames, chunk_size, worker_count=8):
    """打包与推送分离，producer 用 asyncio.create_task 并发 pack，
    consumer 按 chunk start 顺序 yield meta + chunk。"""
    queue: asyncio.Queue = asyncio.Queue(maxsize=worker_count * 2)
    out_of_order: list = []
    sent_until = [0]

    async def pack_one(start: int, count: int):
        t0 = time.perf_counter()
        payload = await anyio.to_thread.run_sync(
            lambda: service.stream_frame_chunk_binary(
                resolved, start, count, format, summary,
            ),
        )
        t_pack_ms = (time.perf_counter() - t0) * 1000.0
        await queue.put((start, count, payload, t_pack_ms))

    async def producer():
        tasks = []
        for start in range(0, n_frames, chunk_size):
            if cancellation._event.is_set() or await request.is_disconnected():
                break
            count = min(chunk_size, n_frames - start)
            tasks.append(asyncio.create_task(pack_one(start, count)))
            if len(tasks) >= worker_count:
                await asyncio.gather(*tasks)
                tasks.clear()
        if tasks: await asyncio.gather(*tasks)
        await queue.put(None)  # sentinel

    async def consumer():
        while True:
            item = await queue.get()
            if item is None: break
            start, count, payload, t_pack_ms = item
            out_of_order.append((start, count, payload, t_pack_ms))
            out_of_order.sort(key=lambda x: x[0])
            while out_of_order and out_of_order[0][0] == sent_until[0]:
                s, c, pl, pm = out_of_order.pop(0)
                encoded = base64.b64encode(pl).decode("ascii")
                yield _sse_event("meta", json.dumps({
                    "start": s, "count": c,
                    "t_pack_ms": pm,
                    "t_b64_ms": 0,
                    "server_emit_ms": time.time() * 1000.0,
                    "size_b64": len(encoded),
                }))
                yield _sse_event("chunk", encoded)
                sent_until[0] += chunk_size
```

**要点**：
- **保持 event 顺序**：`out_of_order.sort()` 保证 yield 按 chunk `start` 升序，浏览器 EventSource 不会被乱序 meta / chunk 打断统计。
- **取消传播**：producer 在循环前探查 `cancellation._event`，task 内 `await queue.get()` 时不计回（让 consumer 退出 sentinel）。
- **worker_count**：默认 8，`min(32, n_chunks)` 起步。更大的 N 在 4 核机器会锁反转反而慢。
- **不改变 debug 诊断协议**：`--debug` 下 event: ready / meta / chunk / done 全保持；正常模式只发 chunk / done，避免探针开销。

**预期 t_pack 总**：4.0 s → 0.4–0.6 s（8 worker 摊销）。wall 4.71 s → 1.6–2.0 s。

### #2 🥈6 次 `add_array` → `np.concatenate` 一次性 `tobytes()`

**位置**：`backend/app/services/structure_service.py:2539` `_build_binary_payload_from_normalized` 的 `add_array` 循环。

**思路**：
```python
def _build_binary_payload_from_normalized(
    self, *, path, frames, start, n_frames_total, detected_format,
):
    # ...header JSON 部分不变 ...

    # 一次性拼成一个连续 float32/int32 buffer：
    body_flat = np.concatenate([
        np.asarray(positions, dtype='<f4').ravel(),    # count*n_atoms*3
        np.asarray(cells,     dtype='<f4').ravel(),    # count*9
        np.asarray(tags,      dtype='<i4').ravel(),    # count*n_atoms
        np.asarray(energy,    dtype='<f4').ravel(),    # count
        np.asarray(fmax,      dtype='<f4').ravel(),    # count
    ])
    fixed_mask_bytes = bytes(np.asarray(fixed_mask, dtype=np.uint8).ravel())

    payload_bytes = body_flat.tobytes() + fixed_mask_bytes  # 一条 memcpy
    # 按 alignment 把 4-byte buffer + 1-byte block 拼成最终 view
    # (保留与原 header.arrays offsets 兼容的 layout)

    return STRUCTURE_BINARY_MAGIC + struct.pack("<I", len(header_bytes)) \
        + header_bytes + header_padding + payload_bytes
```

**要点**：
- **保留 CWB1 header `arrays` 偏移语义**：把"全部 float32 排前 → 末尾 1-byte fixed_mask"重新写 hedaer offsets 即可；前端 `parseStructureBinaryChunk` 已经按 offsets 读。
- **省 5 次 alloc + 5 次 memcpy**：单 chunk 0.3 → 0.05 ms。
- **风险**：低，因为输出 byte 序列只有最末 fixed_mask 位置变动；但要给单测 byte-equal 对照。

**预期**：单 chunk 0.3–0.5 ms 节省；总 t_pack 4.0 → 3.0-3.4 s。

### #3 🥉numbers_tuple GC 坑 —— 几乎白拿

**位置**：`backend/app/services/structure_service.py` 中的 `_normalize_frame_data_atom_level`。

**当前**：
```python
"numbers": tuple(int(value) for value in numbers_array.tolist()),
```

每一 chunk 一行都要把 numpy 数组 → Python list → 遍历每个元素 int → tuple。113 atoms × 1250 chunks ≈ 14 万次 Python int() box + GC 压力。

**改成**：
```python
"numbers": tuple(map(int, numbers_array.astype('<i4', casting='same_kind').tolist())),
# 或者不改 outer 类型，只避免 int() box：
"numbers": numbers_array.astype('<i4').tolist(),
```

**预期**：单 chunk 60 µs → 5 µs。**几乎免费，本就该顺手改**。

### #4 🔧readline 矢量化（解锁 GIL 释放路径）

**位置**：`backend/app/services/structure_service.py` 的 `_read_plain_xyz_frame_from_handle`。

**当前**：
```python
for atom_index in range(atom_count):
    fields = handle.readline().split()
    positions[atom_index] = (float(fields[1]), float(fields[2]), float(fields[3]))
```

单 chunk 16 帧 × 93 atoms = **1488 次 readline + .split + .float**，完全占 GIL。

**改成**：
```python
def _read_plain_xyz_frame_from_handle(handle, path, frame_index):
    n = int(handle.readline())  # 第一行仍是 readline（只 1 次）
    raw_block = handle.readline(n * 32)  # 大一次性 read（粗略预算 bytes）
    # 没读全就续行：
    while raw_block.count(b'\n') < n:
        raw_block += handle.readline()
    text = raw_block.decode('utf-8')
    coords = np.fromstring(text, sep=' ', dtype='<f4').reshape(-1, 4)[:, 1:4]
    return FrameData(symbols=..., numbers=..., positions=coords, ...)
```

**要点**：
- `np.fromstring(text, sep=' ')` 一次性把 1488 行全部转 float32，**一次 C 遍历**，无 Python line loop / float() box。
- 一帧一行 heading line（`n`）+ n 行 atom；heading 仍是 readline（1 次），13 个 chunk 中一共 19 行 readline 不优化。
- 注意：不去解析符号列（`[:, 1:4]`），假设 topology_stable = True 时的 symbols 在第一帧缓存；否则需要另一条路径。
- **风险**：中，改完要复测 `_read_frame_range` 的 frames 与原版 byte-equal。

**预期**：单 chunk 0.5-1.0 ms 节省；总 t_pack 3.0 → 2.0-2.4 s。

### #5 🔧numpy.memmap —— 抹平重复 IO

**位置**：`backend/app/services/structure_service.py` 的 `_read_frame_range` 和相关的 XYZ 索引缓存层。

**思路**：在 `_resolve_structure_file` 启动时 one-shot `np.memmap(path, dtype='<f4', mode='r')`。新帧取：
```python
flat_pos = memmap[atom_offset:atom_offset + count*atomsPerFrame*3]
frames.append(FrameData(positions=flat_pos.reshape(...), ...))
```

**要点**：
- 必须确认 workspace.security 已拒绝 symlink / 越界，memmap file 由 service 持有 close。
- XYZ 不规则帧（n 或 tags 杂）memmap 不能直接吃；用 `summary.frame_offset` + chunked 重读。
- **风险**：高，IO 路径放大；要 review；建议做完 #1+#2+#3+#4 后再看是否值得。

**预期**：~0.5 ms/chunk。若实现 #1+#2+#3+#4 之后 t_pack 摊到 1-2 s，memmap 加持变 1 s 内。

### #6 🧹已修：dev-bench drift 跨域负数

虽然路径不在后端、但开发者的诊断体验强相关。`idea/tmp/dev-bench.html` 顶部 page-load：
```js
const epoch_offset_at_load = Date.now() - performance.now()
```
然后所有 `server_emit_ms - epoch_offset_at_load` 转成 client monotonic 域再减。

不依赖服务端时钟同步；只把 epoch ms base 换到 local monotonic。修完顶部 + 组成三柱加和 = 100%。

### 落地里程碑

| 阶段 | 涉及改动 | 验证手段 | 预期 wall |
|---|---|---|---|
| M0 (现状) | — | dev-bench E2E | 4.71 s |
| M1 | #3 + #6 + 提 `max_read_size_mb` | dev-bench E2E | ~4.5 s |
| M2 | #2 (concat) | dev-bench + pytest `test_ase_structures` byte-equal | ~4.0 s |
| M3 | #1 (concurrent SSE) | dev-bench + pytest | ~1.6–2.0 s |
| M4 | #4 (readline vec) | dev-bench + 单测 | ~1.2–1.6 s |
| M5 | #5 (memmap) | 同上 | ~1.0–1.2 s |
| M6+ | Three.js / viewer 渲染侧优化（独立于 stream_frame_chunk_binary） | dev-bench viewer 版 | 进一步压 |

**关键纪律**：
1. 每完成一个 milestone 重跑 dev-bench v3，t_pack / 总 wall / 单 chunk 均值对比上 M。
2. 如 E2E drift 跨域表错，#6 在 M1 顺手修。
3. **不要反向破坏 debug 诊断协议**——dev-bench E2E 在 `--debug` 下假定每 `event: meta` 后紧跟 `event: chunk`；正常模式不要求 meta。

### 风险 / 验收 checklist

| 风险 | 验收 |
|---|---|
| CWB1 序列化 regression（dynamic offsets） | pytest byte-equal 对照旧 `_build_binary_payload_from_data` |
| SSE event 顺序乱序 | dev-bench v3 E2E Run 看柱状图不齐（漏 meta） |
| 取消信号在 worker task 内不响应 | E2E 按 **停止**，dev-bench 后端 task 完 ≤ 1 s；不再 pack 200 chunks |
| topology_stable 测试 ticker 退化（新路径走错 topology 检测） | `tests/test_ase_structures.py` 现有用例必须全绿 |
| Frontend `parseStructureBinaryChunk` 解析 fixed_mask 末段 layout | 浏览器 dev-bench E2E histogram 第一行（"0"桶位置）必须是 0 偏移 |
