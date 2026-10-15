# -*- coding: utf-8 -*-
"""orchestration/ —— L3 编排与控制层（把「文档里的规矩」下沉成代码里的检查）

本层只下沉三类规则（详见 `README.md`）：

1. **歧义可见化** —— 目录里有多份 PDF / 多份 .dra 时，把事实**写进返回值**，
   而不是让模型自己去 `ls` 猜（`preflight.py`）。
2. **前置自检内建** —— 正式动手前先跑一次零成本预检（`preflight.py`）。
3. **输出不可伪造** —— 报告带 `run_id` 等回执字段（Phase 3）。

★ 不做「铁律 1–10 全部下沉」：那些规则里有大量**语义判断**（读哪一页、这个标注是不是
   参考尺寸），下沉到代码只会得到一棵永远补不完的 if 树。
★ 更**不许「直接拒绝执行」**：同一个目录里出现多份 PDF，在批量模式 B（每个子目录
   各一对 PDF/.dra）下完全合法 —— 本层只负责**上报**歧义，由调用方（模型/用户）确认。

依赖方向：`mcp_server → orchestration → services → Data|knowledge`
本层**只允许** import 标准库与 `services`，禁止 import `mcp_server` 与 `observability`。
"""
