from aibench.extract.filter_rules import rule_filter_draft, rule_filter_text


def test_drop_ops_and_judge():
    d1 = rule_filter_text("帮我巡检 logs/10.78.26.27 目录下的系统日志，评估该服务器磁盘健康状态")
    assert d1.keep is False
    d2 = rule_filter_text("# Agent 任务完成度评测输入\n## 用户输入 诊断网络\nis_correct")
    assert d2.keep is False


def test_keep_coding():
    d = rule_filter_text(
        "排查下是否有使用gitcode/v4的api接口，如果有，切换到v5",
        has_context_files=True,
        product="opencode",
        tool_names=["bash", "read", "edit"],
    )
    assert d.keep is True
    assert d.score >= 1.0


def test_filter_draft():
    draft = {
        "prompt": "实现一个 fizzbuzz 函数并写测试",
        "context": {"files": [{"path": "a.py", "content": "def f():\n  pass\n"}]},
        "metadata": {
            "has_context_files": True,
            "tool_names": ["read", "write"],
            "tags": ["product:opencode"],
        },
    }
    assert rule_filter_draft(draft).keep is True
