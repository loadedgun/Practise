# -*- coding: utf-8 -*-
"""parsers/base.py 基类测试：表头归一化、CSV 多编码读取、字段映射、parse_file 流水线。

base.py 是所有平台解析器的公共底座，field_map 一对一映射与 CSV 编码/分隔符探测
直接决定能否正确解析 Jira/禅道/自定义导出。本测试覆盖：
- norm_header 归一化（去空白/下划线/连字符/斜杠、小写）；
- read_csv 对 utf-8 / GB18030 / 多分隔符的探测；
- match_fields 一对一映射且不重复覆盖；
- parse_file 端到端：缺失关键列告警、空值占位符过滤、必填字段缺失时仍保留记录。
"""
import os

import pytest

from parsers.base import (BaseParser, STANDARD_FIELDS, norm_header,
                          read_csv, read_table)
import normalize as nz


# ---------------------------------------------------------------------------
# norm_header
# ---------------------------------------------------------------------------

class TestNormHeader:
    def test_strips_common_separators(self):
        assert norm_header('Issue Key') == 'issuekey'
        assert norm_header('Bug_编号-ID/名称') == 'bug编号id名称'
        assert norm_header('') == ''

    def test_case_insensitive(self):
        assert norm_header('SEVERITY') == norm_header('severity')


# ---------------------------------------------------------------------------
# read_csv
# ---------------------------------------------------------------------------

class TestReadCsv:
    def test_utf8_csv_comma(self, tmp_path):
        p = tmp_path / 'a.csv'
        p.write_text('ID,标题,状态\n1,登录失败,已关闭\n', encoding='utf-8')
        headers, rows = read_csv(str(p))
        assert headers == ['ID', '标题', '状态']
        assert rows == [{'ID': '1', '标题': '登录失败', '状态': '已关闭'}]

    def test_gb18030_csv(self, tmp_path):
        # 禅道导出常为 GBK 系编码
        p = tmp_path / 'zentao.csv'
        p.write_text('ID,Bug标题,状态\n1,登录失败,已关闭\n', encoding='gb18030')
        headers, rows = read_csv(str(p))
        assert headers == ['ID', 'Bug标题', '状态']
        assert rows[0]['Bug标题'] == '登录失败'

    def test_semicolon_delimiter(self, tmp_path):
        p = tmp_path / 'semi.csv'
        p.write_text('a;b;c\n1;2;3\n', encoding='utf-8')
        _, rows = read_csv(str(p))
        assert rows == [{'a': '1', 'b': '2', 'c': '3'}]

    def test_tab_delimiter(self, tmp_path):
        p = tmp_path / 'tab.csv'
        p.write_text('a\tb\n1\t2\n', encoding='utf-8')
        _, rows = read_csv(str(p))
        assert rows == [{'a': '1', 'b': '2'}]

    def test_fallback_delimiter_when_sniff_fails(self, tmp_path):
        # 单行无明显分隔符 → 回退逗号
        p = tmp_path / 'odd.csv'
        p.write_text('onlyheader\n', encoding='utf-8')
        headers, rows = read_csv(str(p))
        assert headers == ['onlyheader']
        assert rows == []


# ---------------------------------------------------------------------------
# match_fields 一对一映射
# ---------------------------------------------------------------------------

class TestMatchFields:
    def _make_parser(self):
        class P(BaseParser):
            name = 't'
            field_map = {
                'bug_id': ['ID'],
                'title': ['标题', 'Summary'],
                'status': ['状态'],
            }
        return P()

    def test_one_to_one_mapping(self):
        p = self._make_parser()
        mapping = p.match_fields(['ID', '标题', '状态'])
        # mapping: {normed_header: std_field}
        assert mapping['id'] == 'bug_id'
        assert mapping['标题'] == 'title'
        assert mapping['状态'] == 'status'

    def test_duplicate_header_not_remapped(self):
        # 同一标准字段只映射到第一个命中的表头（后匹配不覆盖）
        p = self._make_parser()
        mapping = p.match_fields(['标题', 'Summary', '状态'])
        # title 只映射到第一个命中的「标题」
        assert mapping['标题'] == 'title'
        assert 'summary' not in mapping

    def test_two_headers_to_same_std_field_is_intercepted(self):
        """回归用例：两个不同原始表头同时命中同一标准字段时，仅第一个被保留。

        历史 bug：match_fields 内守卫误写为 `if std in mapping`（检查键），
        导致两个不同表头可同时映射到同一标准字段，parse_file 中后写覆盖前值、
        静默丢数据。修复为 `if std in mapping.values()` 后，第二个表头应被拦截。
        """
        class P(BaseParser):
            name = 't2'
            # bug_id 有两个候选表头：「ID」与「编号」
            field_map = {
                'bug_id': ['ID', '编号'],
                'title': ['标题'],
                'status': ['状态'],
            }
        mapping = P().match_fields(['ID', '编号', '标题', '状态'])

        # 第一个命中的「ID」映射到 bug_id
        assert mapping.get('id') == 'bug_id'
        # 第二个命中同一标准字段的「编号」必须被拦截，不能出现在 mapping 中
        assert '编号' not in mapping
        # mapping 的 values（标准字段）必须两两不同
        values = list(mapping.values())
        assert len(values) == len(set(values)), (
            '标准字段出现重复映射: %s' % values)
        assert set(values) == {'bug_id', 'title', 'status'}

    def test_unmapped_headers_ignored(self):
        p = self._make_parser()
        mapping = p.match_fields(['ID', '标题', '状态', '完全无关列'])
        assert len(mapping) == 3


# ---------------------------------------------------------------------------
# parse_file 端到端
# ---------------------------------------------------------------------------

class TestParseFile:
    def _write_jira_csv(self, tmp_path):
        p = tmp_path / 'jira.csv'
        p.write_text(
            'Issue key,Summary,Status,Severity,Priority,Resolution,Created,Resolved\n'
            'BUG-1,登录失败,Closed,Critical,High,fixed,2026-08-01 10:00:00,2026-08-01 12:00:00\n'
            'BUG-2,,Open,Major,Medium,,,\n'
            ',,, , ,- ,无 ,\n',
            encoding='utf-8',
        )
        return str(p)

    def test_parse_file_maps_and_normalizes(self, tmp_path):
        from parsers.jira import JiraParser
        path = self._write_jira_csv(tmp_path)
        result = JiraParser().parse_file(path)
        assert result['platform'] == 'jira'
        assert result['required_ok'] is True
        bugs = result['bugs']
        # 第 3 行全空（值为 -/无/空白）→ 不产生 bug
        assert len(bugs) == 2
        b1 = bugs[0]
        assert b1['bug_id'] == 'BUG-1'
        assert b1['title'] == '登录失败'
        assert b1['status_group'] == 'closed'
        assert b1['severity_std'] == '严重'
        assert b1['priority_std'] == 'P1'
        assert b1['resolution_std'] == '已修复'
        assert b1['created_at_dt'] is not None

    def test_parse_file_warns_on_missing_required_columns(self, tmp_path):
        from parsers.jira import JiraParser
        p = tmp_path / 'bad.csv'
        p.write_text('Summary,Status\n登录失败,Closed\n', encoding='utf-8')
        result = JiraParser().parse_file(str(p))
        assert result['required_ok'] is False
        assert any('缺少关键列' in w for w in result['warnings'])

    def test_parse_file_empty_file_warns(self, tmp_path):
        from parsers.jira import JiraParser
        p = tmp_path / 'empty.csv'
        p.write_text('Issue key,Summary,Status\n', encoding='utf-8')
        result = JiraParser().parse_file(str(p))
        assert result['bugs'] == []
        assert any('未解析出任何缺陷记录' in w for w in result['warnings'])

    def test_placeholder_values_treated_as_empty(self, tmp_path):
        # '-' 和 '无' 占位符应被过滤为 None
        from parsers.jira import JiraParser
        p = tmp_path / 'ph.csv'
        p.write_text('Issue key,Summary,Status,Resolution\nBUG-9,标题,Closed,-\n', encoding='utf-8')
        result = JiraParser().parse_file(str(p))
        assert result['bugs'][0]['resolution_std'] is None
