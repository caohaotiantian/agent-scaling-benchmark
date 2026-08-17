# `seed-v0` — 接线夹具，**不是 benchmark 用例集**

这四条用例存在的唯一目的，是让 `aibench run` / `ablation` / `validate-cases` 在没有网络、
没有数据库、没有网关的情况下能跑通一遍，从而证明 CLI 的接线是好的。

**它测不出任何东西。** 具体地：

* **过不了 solvability 门禁。** `check_reference_solution` 对其中的用例判 `False` ——
  它们没有可用的参考解。真实用例集里这是拒收理由，这里是刻意保留的现状。
* **`e2e_pipeline.sh --dry-run` 用 mock agent 消融它**，那个 agent 的「解」是写死的常量，
  并且要加 `--allow-weak-grader` 才跑得起来。绿色只说明流程接通了，不说明任何模型能做什么。
* **每个文件都没有 `role`。** 这是合法的（schema 缺省为 `impl`），也是真实语料里不会出现的
  形状 —— 它曾经让 `compose.donor_files` 把 `test_fizzbuzz.py` 当实现文件植入检索用例。
  留着不改：这正是那类缺陷的回归样本。

要看真实的难度分布，去 `benchmarks/ai_coding/calibrations/`（已入库，可独立复算）。
要跑真实用例集，先按 `README.md` 的快速开始生成一批 —— 本仓库不分发任何用例集，
原因见 `.gitignore` 末尾的注释。

引用它的名字时用 `fixture:seed-v0`，可以确保解析到这里而不是某个同名的本地目录。
