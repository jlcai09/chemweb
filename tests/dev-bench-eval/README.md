# tests/dev-bench-eval

结构加载（SSE chunk handler + viewer 渲染链条）专项测试目录。

## 目录内容

| 文件 | 来源 | 用途 |
|---|---|---|
| `dev-bench.md` | 同步自 `idea/performance/dev-bench.md` | 全链路诊断 + 优化方案说明（同一份文，方便这一组 commit 一起走 review） |
| `dev-bench.html` | 同步自 `idea/tmp/dev-bench.html` | Vite dev server 入口（同时保留在前端的工作副本 `frontend/public/_dev/dev-bench.html`） |

## 这个目录解决什么

chemssh 加载 108 MB / 20 000 帧结构文件的体感 5–6 s，前端优化已经做完
但 `sim` 模式只能测到 ~600 ms；中间 ~5 s 花在哪里没工具定位。本目录配套：

- **离线 sim 模式**：页内构造 CWB1 chunks，1:1 镜像 `MoleculeViewer.vue` chunk handler，
  测试前端 7 阶段独立耗时（atob / 1-char 循环 / cwb1 magic / header / views / parse / write_chunk / frame_recon / estimate_bonds）。
- **E2E 模式**：浏览器走 `EventSource('/api/structures/ase/frames.stream?path=…')`，
  后端 SSE 在 `event: ready`/`meta`/`chunk`/`done` 上打 `t_resolve`/`t_scan`/`t_pack`/`t_b64`/`server_emit_ms` 等时间戳；
  dev-bench v3 客户端按收到顺序累加划分：backend（4 拆）· network drift · frontend（4 拆）。

完整文档（双模式详解 · 实测 4.71 s 分解 · 优化路线 #1–#6 · 落地里程碑 · 风险 / 验收 checklist）请看
[`dev-bench.md`](./dev-bench.md)。

## 一句话起测

```powershell
# 在 chemssh 仓库根：
Copy-Item -Force idea\tmp\dev-bench.html frontend\public\_dev\dev-bench.html
# 后端 + Vite 两个 shell 起来后：
#   http://127.0.0.1:5173/_dev/dev-bench.html
```

## 当前里程碑状态（详见 dev-bench.md §8）

| M | 内容 | 状态 |
|---|---|---|
| M0 | 现状 backend t_pack 占 85 % wall | ✅ 已诊断 |
| M1 | #3 numbers_tuple GC + #6 drift 跨域 + 提 `max_read_size_mb` | 🔧 进行中（本 commit） |
| M2 | #2 6× add_array → np.concatenate 一次性 tobytes | ⏳ |
| M3 | #1 SSE event_generator 并发打包（8 worker） | ⏳ |
| M4 | #4 readline 矢量化（bulk bytes + np.fromstring） | ⏳ |
| M5 | #5 numpy.memmap | ⏳ |

## 本目录与其它路径的同步

```
idea/tmp/dev-bench.html      ← 工作源，每次改完 edit 这里
       │
       ▼
idea/performance/dev-bench.md ← 文档同步
       │
       ▼
tests/dev-bench-eval/{dev-bench.html, dev-bench.md}  ← 这套 commit 的镜像
       │
       ▼
frontend/public/_dev/dev-bench.html  ← Vite dev 入口（手动 copy）
```

git 操作由用户手动完成，本目录不直接 commit / push。
