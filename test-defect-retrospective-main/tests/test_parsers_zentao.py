# -*- coding: utf-8 -*-
"""parsers/zentao.py 禅道解析插件测试。

禅道导出值普遍带「(#id)」后缀（如「已关闭(#closed)」「/订单管理(#102)」），
纯引用「(#0)」表示空值。_clean_zentao 与 to_standard 的清洗逻辑直接决定
禅道数据能否被下游 normalize 正确识别，是平台适配的关键边界。
"""
import pytest

from parsers.zentao import ZentaoParser, _clean_zentao


# ---------------------------------------------------------------------------
# _clean_zentao 值清洗
# ---------------------------------------------------------------------------

class TestCleanZentao:
    @pytest.mark.parametrize('raw,expected', [
        (None, None),
        ('', None),
        ('   ', None),
        ('(#0)', None),                      # 纯引用 → 空
        ('(#123)', None),                    # 纯引用（数字 id）→ 空
        ('(0)', None),                       # 无 # 的纯引用也置空
        ('已关闭(#closed)', '已关闭'),
        ('已解决', '已解决'),
        ('/订单管理(#102)', '/订单管理'),
        ('张三(#5)', '张三'),
        ('critical(#1)', 'critical'),
        ('  有空格  ', '有空格'),
    ])
    def test_clean_values(self, raw, expected):
        assert _clean_zentao(raw) == expected

    def test_clean_keeps_inner_parentheses(self):
        # 仅去掉尾部 (#xxx)，中间括号保留
        assert _clean_zentao('登录(失败)问题(#10)') == '登录(失败)问题'


# ---------------------------------------------------------------------------
# ZentaoParser.to_standard
# ---------------------------------------------------------------------------

class TestZentaoToStandard:
    def test_module_leading_slash_stripped(self):
        parser = ZentaoParser()
        std = {'module': '/订单管理/下单', 'status': '已关闭(#closed)',
               'severity': '1', 'priority': '2', 'resolution': '已修复(#fixed)'}
        out = parser.to_standard(std)
        assert out['module'] == '订单管理/下单'
        assert out['status_group'] == 'closed'
        assert out['severity_std'] == '致命'
        assert out['priority_std'] == 'P1'
        assert out['resolution_std'] == '已修复'

    def test_clean_keys_all_present(self):
        # 所有 _CLEAN_KEYS 字段都应被清洗
        parser = ZentaoParser()
        std = {k: '值(#1)' for k in (
            'bug_id', 'title', 'status', 'severity', 'priority', 'module',
            'reporter', 'assignee', 'resolver', 'verifier', 'resolution',
            'root_cause', 'labels', 'requirement_id', 'repro', 'remark',
            'duplicate_of', 'found_version')}
        out = parser.to_standard(std)
        for k in ('bug_id', 'title', 'status', 'severity', 'priority',
                  'reporter', 'assignee', 'resolver', 'verifier', 'resolution',
                  'root_cause', 'requirement_id', 'repro', 'remark',
                  'duplicate_of', 'found_version'):
            assert out[k] == '值', f'{k} 未正确清洗'


# ---------------------------------------------------------------------------
# parse_file 端到端（禅道 CSV）
# ---------------------------------------------------------------------------

class TestZentaoParseFile:
    def _write_zentao_csv(self, tmp_path):
        p = tmp_path / 'zentao.csv'
        p.write_text(
            'ID,Bug标题,状态,严重程度,优先级,解决方案,创建者,指派给,'
            '由谁解决,关闭者,创建日期,解决日期,根因,激活次数\n'
            '1,登录失败,已关闭(#closed),1,2,已修复(#fixed),张三(#5),李四(#6),'
            '李四(#6),王五(#7),2026-08-01 10:00:00,2026-08-01 12:00:00,空指针,0\n'
            '2,(#0),已解决,2,3,,(#0),(#0),(#0),(#0),,,(#0),1\n',
            encoding='utf-8',
        )
        return str(p)

    def test_parse_cleans_and_normalizes(self, tmp_path):
        path = self._write_zentao_csv(tmp_path)
        result = ZentaoParser().parse_file(path)
        assert result['platform'] == 'zentao'
        assert result['required_ok'] is True
        bugs = result['bugs']
        # 第 2 行 title 为 (#0) → 清洗后为空，但 bug_id=2 存在 → 仍保留
        assert len(bugs) == 2
        b1 = bugs[0]
        assert b1['bug_id'] == '1'
        assert b1['title'] == '登录失败'
        assert b1['status_group'] == 'closed'
        assert b1['severity_std'] == '致命'
        assert b1['priority_std'] == 'P1'
        assert b1['resolution_std'] == '已修复'
        assert b1['reporter'] == '张三'
        assert b1['root_cause_std'] == '代码逻辑'
        assert b1['reopen_count'] == 0

        b2 = bugs[1]
        # title 清洗后为空
        assert b2['title'] is None
        assert b2['status_group'] == 'resolved'
        assert b2['reopen_count'] == 1
