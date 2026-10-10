# -*- coding: utf-8 -*-
"""normalize.py 核心标准化模块测试。

normalize.py 是所有平台解析器输出的统一下游：状态/严重度/优先级/解决结果/根因/偶现/
时间/标题指纹/版本排序等口径都在此集中定义。本测试覆盖：
- 两轮匹配（精确 → 包含）的正确性与回归安全；
- 无效缺陷判定、根因关键词归类、偶现概率解析等业务规则边界；
- 多格式日期时间解析（含 Jira ISO、中文、空值、非法值）；
- 标题相似度、版本语义排序键、标签字段清洗；
- normalize_bug 全流程派生字段。
"""
from datetime import datetime

import pytest

import normalize as nz


# ---------------------------------------------------------------------------
# _norm / _match_group 基础匹配机制
# ---------------------------------------------------------------------------

class TestNormAndMatchGroup:
    def test_norm_strips_whitespace_and_lowercases(self):
        assert nz._norm('  Closed  ') == 'closed'
        assert nz._norm('已 关 闭') == '已关闭'
        assert nz._norm(None) == ''
        assert nz._norm('') == ''
        assert nz._norm(123) == '123'

    def test_match_group_none_or_empty_returns_default(self):
        assert nz._match_group(None, nz._STATUS_GROUPS, 'unknown') == 'unknown'
        assert nz._match_group('', nz._STATUS_GROUPS, 'unknown') == 'unknown'
        assert nz._match_group('   ', nz._STATUS_GROUPS, 'unknown') == 'unknown'

    def test_match_group_exact_match_prefers_first(self):
        # 「已关闭」同时可能被 closed 命中，应返回 closed（第一个精确命中的组）
        assert nz.status_group('已关闭') == 'closed'
        assert nz.severity_std('1') == '致命'
        assert nz.priority_std('1') == 'P0'

    def test_match_group_contains_match_for_parenthesized_values(self):
        # 平台常见导出形如「已关闭(Closed)」，精确匹配失败后走包含匹配
        assert nz.status_group('已关闭(Closed)') == 'closed'
        assert nz.status_group('处理中(In Progress)') == 'in_progress'

    def test_match_group_unknown_returns_default(self):
        assert nz.status_group('完全未知的状态') == 'unknown'
        assert nz.severity_std('S9') == '未分级'
        assert nz.priority_std('P9') == '未分级'


# ---------------------------------------------------------------------------
# 状态分组
# ---------------------------------------------------------------------------

class TestStatusGroup:
    @pytest.mark.parametrize('raw,expected', [
        ('closed', 'closed'),
        ('已关闭', 'closed'),
        ('已验证通过', 'closed'),
        ('verified', 'closed'),
        ('已验收', 'closed'),
        ('resolved', 'resolved'),
        ('已解决', 'resolved'),
        ('待验证', 'resolved'),
        ('in progress', 'in_progress'),
        ('处理中', 'in_progress'),
        ('重新打开', 'in_progress'),
        ('open', 'open'),
        ('新建', 'open'),
        ('已延期', 'open'),
        (None, 'unknown'),
        ('', 'unknown'),
    ])
    def test_status_mapping(self, raw, expected):
        assert nz.status_group(raw) == expected


# ---------------------------------------------------------------------------
# 严重度 / 优先级（含禅道数字 1-4 映射）
# ---------------------------------------------------------------------------

class TestSeverityAndPriority:
    @pytest.mark.parametrize('raw,expected', [
        ('1', '致命'), ('fatal', '致命'), ('blocker', '致命'),
        ('2', '严重'), ('critical', '严重'),
        ('3', '一般'), ('major', '一般'),
        ('4', '轻微'), ('minor', '轻微'),
        ('5', '建议'), ('trivial', '建议'),
        (None, '未分级'),
        ('未知级别', '未分级'),
    ])
    def test_severity_mapping(self, raw, expected):
        assert nz.severity_std(raw) == expected

    @pytest.mark.parametrize('raw,expected', [
        ('1', 'P0'), ('urgent', 'P0'), ('立即处理', 'P0'),
        ('2', 'P1'), ('高', 'P1'),
        ('3', 'P2'), ('medium', 'P2'),
        ('4', 'P3'), ('low', 'P3'), ('trivial', 'P3'),
        (None, '未分级'),
    ])
    def test_priority_mapping(self, raw, expected):
        assert nz.priority_std(raw) == expected


# ---------------------------------------------------------------------------
# 解决结果 / 无效缺陷
# ---------------------------------------------------------------------------

class TestResolution:
    @pytest.mark.parametrize('raw,expected', [
        ('fixed', '已修复'),
        ('已修复', '已修复'),
        ('duplicate', '重复'),
        ('重复提交', '重复'),
        ("won't fix", '设计如此'),
        ('wontfix', '设计如此'),
        ('cannot reproduce', '无法重现'),
        ('invalid', '无效'),
        ('转需求', '转需求'),
        ('延期', '延期处理'),
        (None, None),
        ('', None),
        ('未说明原因', '其他'),
    ])
    def test_resolution_mapping(self, raw, expected):
        assert nz.resolution_std(raw) == expected

    @pytest.mark.parametrize('resolution,expected', [
        ('重复', True),
        ('设计如此', True),
        ('无法重现', True),
        ('无效', True),
        ('转需求', True),
        ('已修复', False),
        ('延期处理', False),
        (None, False),
    ])
    def test_is_invalid_bug(self, resolution, expected):
        assert nz.is_invalid_bug({'resolution_std': resolution}) == expected


# ---------------------------------------------------------------------------
# 根因归类
# ---------------------------------------------------------------------------

class TestRootCause:
    @pytest.mark.parametrize('raw,expected', [
        ('需求理解偏差', '需求问题'),
        ('空指针异常', '代码逻辑'),
        ('服务器配置错误', '环境配置'),
        ('脏数据导致', '数据问题'),
        ('架构设计不合理', '设计缺陷'),
        ('浏览器兼容性', '兼容性问题'),
        ('误操作', '操作失误'),
        (None, None),
        ('', None),
        ('说不清道不明的原因', '未分类'),
    ])
    def test_root_cause_mapping(self, raw, expected):
        assert nz.root_cause_std(raw) == expected


# ---------------------------------------------------------------------------
# 偶现缺陷识别
# ---------------------------------------------------------------------------

class TestOccasional:
    @pytest.mark.parametrize('repro,texts,expected', [
        ('偶现', (), True),
        ('难以复现', (), True),
        ('复现率 25%', (), True),
        ('<30%', (), True),
        ('低于30%', (), True),
        ('小于30%', (), True),
        ('100%', (), False),
        ('必现', (), False),
        (None, (), False),
        ('', (), False),
    ])
    def test_is_occasional(self, repro, texts, expected):
        assert nz.is_occasional(repro, *texts) == expected

    def test_occasional_text_fallback(self):
        # 复现概率字段为空，但备注/标题含偶现关键词
        assert nz.is_occasional(None, '偶现一次') is True
        assert nz.is_occasional('100%', '标题正常') is False

    def test_occasional_probability_edge(self):
        # 边界：30% 不算偶现（<30 才是），29% 算
        assert nz.is_occasional('30%') is False
        assert nz.is_occasional('29%') is True


# ---------------------------------------------------------------------------
# 日期时间解析
# ---------------------------------------------------------------------------

class TestParseDatetime:
    @pytest.mark.parametrize('raw,expected', [
        ('2026-08-30 11:00:00', datetime(2026, 8, 30, 11, 0, 0)),
        ('2026-08-30 11:00', datetime(2026, 8, 30, 11, 0)),
        ('2026-08-30', datetime(2026, 8, 30)),
        ('2026/08/30 11:00:00', datetime(2026, 8, 30, 11, 0, 0)),
        ('2026年08月30日', datetime(2026, 8, 30)),
        ('2026.08.30 11:00:00', datetime(2026, 8, 30, 11, 0, 0)),
        (None, None),
        ('', None),
    ])
    def test_common_formats(self, raw, expected):
        assert nz.parse_datetime(raw) == expected

    def test_jira_iso_format(self):
        # Jira 导出带毫秒与时区：截取前 19 位
        dt = nz.parse_datetime('2026-08-30T11:00:00.000+0800')
        assert dt == datetime(2026, 8, 30, 11, 0, 0)

    def test_datetime_passthrough(self):
        now = datetime.now()
        assert nz.parse_datetime(now) is now

    def test_unparseable_returns_none(self):
        assert nz.parse_datetime('not a date') is None


# ---------------------------------------------------------------------------
# 标题指纹 / 相似度
# ---------------------------------------------------------------------------

class TestTitleFingerprint:
    def test_fingerprint_strips_punctuation_and_spaces(self):
        assert nz.title_fingerprint('登录失败，提示错误！') == '登录失败提示错误'
        assert nz.title_fingerprint('  Hello, World!  ') == 'helloworld'

    def test_fingerprint_none(self):
        assert nz.title_fingerprint(None) == ''

    def test_similarity_identical(self):
        assert nz.title_similarity('登录失败', '登录失败') == 1.0

    def test_similarity_empty(self):
        assert nz.title_similarity('', '登录失败') == 0.0
        assert nz.title_similarity(None, '登录失败') == 0.0

    def test_similarity_deterministic_partial(self):
        # 同输入必同输出；部分相似应在 (0, 1)
        s = nz.title_similarity('登录失败提示错误', '登录成功提示成功')
        assert 0.0 < s < 1.0


# ---------------------------------------------------------------------------
# 版本语义排序键
# ---------------------------------------------------------------------------

class TestVersionKey:
    def test_semantic_version_ordering(self):
        # v2.10.0 应大于 v2.9.0（按数字段比较，非字符串）
        versions = ['v2.9.0', 'v2.10.0', 'v2.1.0']
        assert sorted(versions, key=nz.version_key) == ['v2.1.0', 'v2.9.0', 'v2.10.0']

    def test_version_key_handles_none(self):
        assert nz.version_key(None) == []
        assert nz.version_key('') == []


# ---------------------------------------------------------------------------
# 标签字段
# ---------------------------------------------------------------------------

class TestLabelsList:
    def test_string_split(self):
        assert nz.labels_list({'labels': 'a, b; c，d'}) == ['a', 'b', 'c', 'd']

    def test_list_input(self):
        assert nz.labels_list({'labels': ['a', ' b ', '']}) == ['a', 'b']

    def test_none_returns_empty(self):
        assert nz.labels_list({}) == []
        assert nz.labels_list({'labels': None}) == []


# ---------------------------------------------------------------------------
# normalize_bug 全流程
# ---------------------------------------------------------------------------

class TestNormalizeBug:
    def test_full_pipeline_derives_standard_fields(self):
        bug = {
            'status': '已关闭(Closed)',
            'severity': '2',
            'priority': '1',
            'resolution': '已修复',
            'root_cause': '空指针',
            'repro': '偶现',
            'remark': '低概率',
            'title': '登录失败',
            'created_at': '2026-08-30 10:00:00',
            'resolved_at': '2026-08-30 12:00:00',
            'closed_at': '2026-08-30 14:00:00',
            'reopen_count': '2',
            'duplicate_of': '',
        }
        out = nz.normalize_bug(bug)
        assert out['status_group'] == 'closed'
        assert out['severity_std'] == '严重'
        assert out['priority_std'] == 'P0'
        assert out['resolution_std'] == '已修复'
        assert out['root_cause_std'] == '代码逻辑'
        assert out['is_occasional'] is True
        assert out['is_duplicate_flag'] is False
        assert out['created_at_dt'] == datetime(2026, 8, 30, 10, 0, 0)
        assert out['reopen_count'] == 2

    def test_duplicate_flag_via_resolution(self):
        bug = {'resolution': '重复', 'duplicate_of': None}
        assert nz.normalize_bug(bug)['is_duplicate_flag'] is True

    def test_duplicate_flag_via_duplicate_of(self):
        bug = {'resolution': '已修复', 'duplicate_of': 'BUG-123'}
        assert nz.normalize_bug(bug)['is_duplicate_flag'] is True

    def test_reopen_count_invalid_falls_back_to_zero(self):
        bug = {'reopen_count': 'abc'}
        assert nz.normalize_bug(bug)['reopen_count'] == 0

    def test_reopen_count_none_is_zero(self):
        bug = {}
        assert nz.normalize_bug(bug)['reopen_count'] == 0
