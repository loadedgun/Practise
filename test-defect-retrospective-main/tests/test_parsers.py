# -*- coding: utf-8 -*-
"""parsers 包测试：基类字段映射、CSV 读取（编码/分隔符探测）、parse_file 集成
（占位值过滤、关键列缺失告警、空行跳过）、平台自动探测阈值、禅道 (#id) 后缀清洗。

这些路径是所有缺陷数据进入分析管线的入口，映射错位或清洗遗漏会污染下游全部指标。
"""
import os
import pytest

import normalize as nz
from parsers import get_parser, detect_platform, PARSER_REGISTRY
from parsers.base import BaseParser, norm_header, read_csv, read_table
from parsers.jira import JiraParser
from parsers.zentao import ZentaoParser, _clean_zentao


# ---------------------------------------------------------------------------
# norm_header：表头归一化
# ---------------------------------------------------------------------------
class TestNormHeader:
    def test_strips_whitespace_lowercases(self):
        assert norm_header(' Issue Key ') == 'issuekey'

    def test_removes_underscore_dash_slash(self):
        assert norm_header('A_B-C/D') == 'abcd'

    def test_empty(self):
        assert norm_header('') == ''


# ---------------------------------------------------------------------------
# BaseParser.match_fields：一对一映射、后匹配不覆盖、首次命中
# ---------------------------------------------------------------------------
class TestMatchFields:
    def test_jira_maps_standard_headers(self):
        p = JiraParser()
        headers = ['Issue key', 'Summary', 'Status', 'Severity', 'Priority']
        mapping = p.match_fields(headers)
        assert mapping['issuekey'] == 'bug_id'
        assert mapping['summary'] == 'title'
        assert mapping['status'] == 'status'
        assert mapping['severity'] == 'severity'
        assert mapping['priority'] == 'priority'

    def test_duplicate_field_candidates_both_mapped(self):
        # 文档约定「一对一，后匹配不覆盖」，但 match_fields 的守卫 `if std in mapping`
        # 检查的是键（归一化表头）而非值（标准字段），因此两个候选表头会被同时映射
        # 到同一标准字段；parse_file 中后处理的表头值会覆盖先处理的。
        # 该差异已在本次复盘总结中标记为待修复项，此处锁定现状以防进一步漂移。
        p = JiraParser()
        mapping = p.match_fields(['Summary', '摘要'])
        assert mapping['summary'] == 'title'
        assert mapping['摘要'] == 'title'

    def test_unmapped_headers_excluded(self):
        p = JiraParser()
        mapping = p.match_fields(['Issue key', '随机无关列'])
        assert '随机无关列' not in {v for v in mapping}

    def test_empty_headers_skipped(self):
        p = JiraParser()
        mapping = p.match_fields(['', None, 'Status'])
        assert 'status' in mapping.values()


# ---------------------------------------------------------------------------
# read_csv / read_table：编码探测、分隔符探测、扩展名分派
# ---------------------------------------------------------------------------
class TestReadCsv:
    def test_utf8_with_bom(self, tmp_path):
        path = tmp_path / 'bom.csv'
        path.write_bytes('\ufeffbug_id,title\nB1,t1\n'.encode('utf-8'))
        headers, rows = read_csv(str(path))
        assert headers[0] == 'bug_id'
        assert rows[0]['bug_id'] == 'B1'

    def test_gb18030_encoding(self, tmp_path):
        path = tmp_path / 'gb.csv'
        path.write_bytes('bug_id,title\nB1,标题中文\n'.encode('gb18030'))
        _, rows = read_csv(str(path))
        assert rows[0]['title'] == '标题中文'

    def test_semicolon_delimiter_detected(self, tmp_path):
        path = tmp_path / 'semi.csv'
        path.write_text('bug_id;title\nB1;t1\n', encoding='utf-8')
        _, rows = read_csv(str(path))
        assert rows[0]['bug_id'] == 'B1'
        assert rows[0]['title'] == 't1'

    def test_read_table_dispatches_by_extension(self, tmp_path):
        path = tmp_path / 't.csv'
        path.write_text('bug_id,title\nB1,t1\n', encoding='utf-8')
        headers, rows = read_table(str(path))
        assert 'bug_id' in headers
        assert rows[0]['title'] == 't1'


# ---------------------------------------------------------------------------
# BaseParser.parse_file：集成（占位值过滤、关键列缺失告警、空行/无记录告警）
# ---------------------------------------------------------------------------
class TestParseFile:
    def _write(self, tmp_path, name, content):
        path = tmp_path / name
        path.write_text(content, encoding='utf-8')
        return str(path)

    def test_filters_placeholder_values(self, tmp_path):
        # 值为 None/''/'无'/'-' 的字段应被置为 None（不进入标准数据集）
        csv_text = ('Issue key,Summary,Status,Resolution\n'
                    'B1,t1,已关闭,无\n'
                    'B2,t2,已关闭,-\n')
        path = self._write(tmp_path, 'bugs.csv', csv_text)
        result = JiraParser().parse_file(path)
        assert result['required_ok'] is True
        assert len(result['bugs']) == 2
        assert result['bugs'][0]['resolution'] is None  # '无' 被过滤
        assert result['bugs'][1]['resolution'] is None  # '-' 被过滤

    def test_missing_required_columns_warns(self, tmp_path):
        # 缺少 bug_id/title/status 任一 → required_ok=False 并告警
        csv_text = 'Issue key,Summary\nB1,t1\n'  # 缺 status
        path = self._write(tmp_path, 'bugs.csv', csv_text)
        result = JiraParser().parse_file(path)
        assert result['required_ok'] is False
        assert any('status' in w for w in result['warnings'])

    def test_empty_data_warns_no_records(self, tmp_path):
        # 只有表头无数据行 → 告警「未解析出任何缺陷记录」
        path = self._write(tmp_path, 'bugs.csv', 'Issue key,Summary,Status\n')
        result = JiraParser().parse_file(path)
        assert result['bugs'] == []
        assert any('未解析出' in w for w in result['warnings'])

    def test_rows_without_bugid_or_title_skipped(self, tmp_path):
        # bug_id 与 title 都为空（占位值）的行不应产出 bug 记录
        csv_text = ('Issue key,Summary,Status\n'
                    'B1,t1,已关闭\n'
                    '无,-,已关闭\n')
        path = self._write(tmp_path, 'bugs.csv', csv_text)
        result = JiraParser().parse_file(path)
        assert len(result['bugs']) == 1
        assert result['bugs'][0]['bug_id'] == 'B1'

    def test_to_standard_invokes_normalization(self, tmp_path):
        # parse_file 应通过 to_standard 钩子写入 *_std 派生字段
        csv_text = ('Issue key,Summary,Status,Severity\nB1,t1,已关闭,1\n')
        path = self._write(tmp_path, 'bugs.csv', csv_text)
        bug = JiraParser().parse_file(path)['bugs'][0]
        assert bug['status_group'] == 'closed'
        assert bug['severity_std'] == '致命'

    def test_source_file_and_row_tracked(self, tmp_path):
        csv_text = 'Issue key,Summary,Status\nB1,t1,已关闭\n'
        path = self._write(tmp_path, 'bugs.csv', csv_text)
        bug = JiraParser().parse_file(path)['bugs'][0]
        assert bug['_source_file'] == 'bugs.csv'
        assert bug['_source_row'] == 2  # 表头是第1行，数据从第2行起


# ---------------------------------------------------------------------------
# detect_platform：表头特征自动探测（Jira 1 命中 / 禅道 2 命中 / 都不中 None）
# ---------------------------------------------------------------------------
class TestDetectPlatform:
    def test_jira_single_signature_hit(self):
        # Jira 特征命中 1 个即判定
        assert detect_platform(['Issue key', '其他无关列']) == 'jira'

    def test_zentao_needs_two_hits(self):
        # 禅道特征需命中 2 个
        assert detect_platform(['Bug标题']) is None
        assert detect_platform(['Bug标题', '严重程度']) == 'zentao'

    def test_unknown_returns_none(self):
        assert detect_platform(['随便', '列名']) is None


# ---------------------------------------------------------------------------
# get_parser：注册表查找与错误
# ---------------------------------------------------------------------------
class TestGetParser:
    def test_known_parsers(self):
        assert isinstance(get_parser('jira'), JiraParser)
        assert isinstance(get_parser('zentao'), ZentaoParser)

    def test_unknown_raises(self):
        with pytest.raises(RuntimeError, match='未知解析平台'):
            get_parser('nonsense')


# ---------------------------------------------------------------------------
# _clean_zentao：禅道 (#id) 后缀清洗（纯引用置空 / 去后缀 / 空值）
# ---------------------------------------------------------------------------
class TestCleanZentao:
    def test_pure_reference_to_none(self):
        # 纯引用 "(#0)" → None
        assert _clean_zentao('(#0)') is None
        assert _clean_zentao('(#closed)') is None

    def test_strips_trailing_id_suffix(self):
        assert _clean_zentao('已关闭(#closed)') == '已关闭'
        assert _clean_zentao('/订单管理(#102)') == '/订单管理'

    def test_plain_value_unchanged(self):
        assert _clean_zentao('激活') == '激活'

    def test_none_and_empty(self):
        assert _clean_zentao(None) is None
        assert _clean_zentao('') is None
        assert _clean_zentao('   ') is None


# ---------------------------------------------------------------------------
# ZentaoParser.to_standard：清洗后标准化 + 模块去前导斜杠
# ---------------------------------------------------------------------------
class TestZentaoToStandard:
    def test_cleans_suffix_then_normalizes(self):
        bug = ZentaoParser().to_standard({
            'bug_id': '102(#102)', 'status': '已关闭(#closed)',
            'severity': '1', 'priority': '1',
        })
        assert bug['bug_id'] == '102'
        assert bug['status_group'] == 'closed'
        assert bug['severity_std'] == '致命'

    def test_module_leading_slash_stripped(self):
        bug = ZentaoParser().to_standard({'module': '/订单管理(#102)'})
        assert bug['module'] == '订单管理'

    def test_pure_reference_id_cleared(self):
        # 模块为纯引用 (#0) → 经 _clean_zentao 置空，再 lstrip('/') 仍为 None
        bug = ZentaoParser().to_standard({'module': '(#0)'})
        assert bug['module'] is None
