# -*- coding: utf-8 -*-
"""normalize.py 是全平台解析器与 analyzer 共用的值级标准化核心模块：
任何下游口径（解决率 / 严重度分布 / 根因 / 偶现 / 时效）都依赖这里的归一化结果。
本测试覆盖别名匹配（精确 + 包含）、多格式日期解析、偶现概率阈值、版本语义排序键、
相似度指纹与 normalize_bug 集成钩子等高风险路径，确保口径改动可被立即捕获。
"""
import pytest
from datetime import datetime

import normalize as nz


# ---------------------------------------------------------------------------
# status_group / severity_std / priority_std / resolution_std：别名归一化
# ---------------------------------------------------------------------------
class TestStatusGroup:
    def test_closed_aliases_exact(self):
        for raw in ('Closed', 'closed', 'CLOSED', '已关闭', ' 已验证 '):
            assert nz.status_group(raw) == 'closed'

    def test_resolved_aliases(self):
        for raw in ('已解决', '待验证', 'ready for qa', 'in qa'):
            assert nz.status_group(raw) == 'resolved'

    def test_in_progress_aliases(self):
        for raw in ('处理中', '重新打开', 'reopened'):
            assert nz.status_group(raw) == 'in_progress'

    def test_open_aliases(self):
        for raw in ('to do', '待办', '新建', '已延期'):
            assert nz.status_group(raw) == 'open'

    def test_contains_match_with_extra_text(self):
        # 「已关闭(Closed)」含已关闭 → closed；包含匹配是第二轮回退
        assert nz.status_group('已关闭(Closed)') == 'closed'

    def test_unknown_and_empty(self):
        assert nz.status_group('奇怪的状态') == 'unknown'
        assert nz.status_group(None) == 'unknown'
        assert nz.status_group('') == 'unknown'
        assert nz.status_group('   ') == 'unknown'


class TestSeverityStd:
    @pytest.mark.parametrize('raw,expected', [
        ('致命', '致命'), ('fatal', '致命'), ('blocker', '致命'), ('1', '致命'),
        ('严重', '严重'), ('critical', '严重'), ('s2', '严重'), ('2', '严重'),
        ('一般', '一般'), ('major', '一般'), ('3', '一般'),
        ('轻微', '轻微'), ('minor', '轻微'), ('4', '轻微'),
        ('建议', '建议'), ('trivial', '建议'), ('5', '建议'),
    ])
    def test_aliases(self, raw, expected):
        assert nz.severity_std(raw) == expected

    def test_ungraded(self):
        assert nz.severity_std('未知') == '未分级'
        assert nz.severity_std(None) == '未分级'


class TestPriorityStd:
    @pytest.mark.parametrize('raw,expected', [
        ('P0', 'P0'), ('highest', 'P0'), ('紧急', 'P0'), ('1', 'P0'),
        ('P1', 'P1'), ('high', 'P1'), ('2', 'P1'),
        ('P2', 'P2'), ('medium', 'P2'), ('3', 'P2'),
        ('P3', 'P3'), ('low', 'P3'), ('4', 'P3'),
    ])
    def test_aliases(self, raw, expected):
        assert nz.priority_std(raw) == expected

    def test_ungraded(self):
        assert nz.priority_std('???') == '未分级'


class TestResolutionStd:
    def test_fixed_aliases(self):
        for raw in ('fixed', '已修复', '修复', '解决', 'done'):
            assert nz.resolution_std(raw) == '已修复'

    def test_duplicate_and_others(self):
        assert nz.resolution_std('重复') == '重复'
        assert nz.resolution_std('Duplicate') == '重复'
        assert nz.resolution_std("won't fix") == '设计如此'
        assert nz.resolution_std('无法重现') == '无法重现'
        assert nz.resolution_std('invalid') == '无效'
        assert nz.resolution_std('延期处理') == '延期处理'

    def test_contains_match(self):
        # 含别名子串
        assert nz.resolution_std('已修复（线下）') == '已修复'

    def test_other_fallback(self):
        assert nz.resolution_std('奇葩结果') == '其他'

    def test_none_and_empty(self):
        assert nz.resolution_std(None) is None
        assert nz.resolution_std('') is None
        assert nz.resolution_std('   ') is None


class TestIsInvalidBug:
    def test_invalid_resolutions(self):
        for res in ('重复', '设计如此', '无法重现', '无效', '转需求'):
            assert nz.is_invalid_bug({'resolution_std': res}) is True

    def test_valid_resolutions(self):
        for res in ('已修复', '延期处理', None, '其他'):
            assert nz.is_invalid_bug({'resolution_std': res}) is False

    def test_missing_field(self):
        assert nz.is_invalid_bug({}) is False


# ---------------------------------------------------------------------------
# root_cause_std：关键词分类（7 类 + 未分类 + None）
# ---------------------------------------------------------------------------
class TestRootCauseStd:
    @pytest.mark.parametrize('raw,expected', [
        ('需求不清', '需求问题'), ('需求变更', '需求问题'), ('prd 遗漏', '需求问题'),
        ('代码逻辑错误', '代码逻辑'), ('空指针异常', '代码逻辑'), ('null pointer', '代码逻辑'),
        ('环境配置问题', '环境配置'), ('部署失败', '环境配置'), ('服务器宕机', '环境配置'),
        ('脏数据', '数据问题'), ('缓存数据错误', '数据问题'),
        ('方案设计缺陷', '设计缺陷'), ('架构不合理', '设计缺陷'),
        ('浏览器兼容', '兼容性问题'), ('分辨率适配', '兼容性问题'),
        ('误操作', '操作失误'), ('人为改动', '操作失误'),
    ])
    def test_keyword_match(self, raw, expected):
        assert nz.root_cause_std(raw) == expected

    def test_default_uncategorized(self):
        assert nz.root_cause_std('一些无法归类的根因文本') == '未分类'

    def test_none_and_empty(self):
        assert nz.root_cause_std(None) is None
        assert nz.root_cause_std('') is None
        assert nz.root_cause_std('   ') is None


# ---------------------------------------------------------------------------
# is_occasional：偶现缺陷判定（关键词 / 概率数字 / 阈值表达式）
# ---------------------------------------------------------------------------
class TestIsOccasional:
    def test_keyword_hit(self):
        assert nz.is_occasional('偶现') is True
        assert nz.is_occasional('复现率低') is True
        assert nz.is_occasional(None, '难以复现的偶发现象') is True

    def test_probability_below_30(self):
        assert nz.is_occasional('复现率 20%') is True
        assert nz.is_occasional('概率：29%') is True

    def test_probability_at_or_above_30(self):
        assert nz.is_occasional('复现率 30%') is False
        assert nz.is_occasional('复现率 100%') is False

    def test_low_prob_expression(self):
        assert nz.is_occasional('<30%') is True
        assert nz.is_occasional('小于 30%') is True
        assert nz.is_occasional('低于30%') is True

    def test_no_signal(self):
        assert nz.is_occasional('稳定复现') is False
        assert nz.is_occasional(None, None, None) is False
        assert nz.is_occasional('') is False

    def test_multiple_texts_any_hit(self):
        # repro 无信号，但 remark 命中关键词 → True
        assert nz.is_occasional('100%', '该问题偶发') is True


# ---------------------------------------------------------------------------
# parse_datetime：多格式解析 + Jira ISO 截取 + 边界
# ---------------------------------------------------------------------------
class TestParseDatetime:
    @pytest.mark.parametrize('raw,expected', [
        ('2026-09-01 10:00:00', datetime(2026, 9, 1, 10, 0, 0)),
        ('2026-09-01 10:00', datetime(2026, 9, 1, 10, 0)),
        ('2026-09-01', datetime(2026, 9, 1)),
        ('2026/09/01 10:00:00', datetime(2026, 9, 1, 10, 0, 0)),
        ('2026年09月01日', datetime(2026, 9, 1)),
        ('2026.09.01', datetime(2026, 9, 1)),
    ])
    def test_formats(self, raw, expected):
        assert nz.parse_datetime(raw) == expected

    def test_jira_iso_truncates_millis_tz(self):
        # Jira 导出 2026-08-30T11:00:00.000+0800 → 截取到秒
        assert nz.parse_datetime('2026-08-30T11:00:00.000+0800') == \
            datetime(2026, 8, 30, 11, 0, 0)

    def test_datetime_passthrough(self):
        dt = datetime(2026, 1, 2, 3, 4, 5)
        assert nz.parse_datetime(dt) is dt

    def test_invalid_returns_none(self):
        assert nz.parse_datetime('not a date') is None
        assert nz.parse_datetime('2026-13-99') is None

    def test_none_and_empty(self):
        assert nz.parse_datetime(None) is None
        assert nz.parse_datetime('') is None
        assert nz.parse_datetime('   ') is None


# ---------------------------------------------------------------------------
# title_fingerprint / title_similarity：去标点、空值、确定性
# ---------------------------------------------------------------------------
class TestTitleSimilarity:
    def test_identical_titles_full_match(self):
        assert nz.title_similarity('登录失败', '登录失败') == 1.0

    def test_punctuation_and_case_ignored(self):
        # 去标点 + 转小写后相同 → 1.0
        assert nz.title_similarity('Login, Failed!', 'login failed') == 1.0

    def test_different_titles_partial(self):
        s = nz.title_similarity('登录失败', '登出成功')
        assert 0.0 < s < 1.0

    def test_empty_returns_zero(self):
        assert nz.title_similarity('', '任何') == 0.0
        assert nz.title_similarity(None, '任何') == 0.0
        assert nz.title_similarity('!!!', '???') == 0.0  # 指纹全为空

    def test_deterministic(self):
        # difflib SequenceMatcher 确定性：同输入同输出
        assert nz.title_similarity('缺陷A', '缺陷B') == nz.title_similarity('缺陷A', '缺陷B')


# ---------------------------------------------------------------------------
# version_key：语义版本排序键（v2.10.0 > v2.9.0 是核心防回归点）
# ---------------------------------------------------------------------------
class TestVersionKey:
    def test_numeric_segments_sort_semantically(self):
        # v2.10.0 应 > v2.9.0（字符串排序会反）
        assert nz.version_key('v2.10.0') > nz.version_key('v2.9.0')
        assert sorted(['v2.9.0', 'v2.10.0', 'v2.1.0'],
                       key=nz.version_key) == ['v2.1.0', 'v2.9.0', 'v2.10.0']

    def test_prefixed_v_handled(self):
        # 数字段以 (1,int,'') 形式参与比较，与非数字 (0,0,str) 可比
        assert nz.version_key('v1.0') < nz.version_key('v2.0')

    def test_empty_and_none(self):
        # 空输入返回空键列表，不抛异常
        assert nz.version_key('') == []
        assert nz.version_key(None) == []


# ---------------------------------------------------------------------------
# labels_list：字符串/列表归一
# ---------------------------------------------------------------------------
class TestLabelsList:
    def test_string_split_mixed_separators(self):
        result = nz.labels_list({'labels': 'a,b；c d，e'})
        assert result == ['a', 'b', 'c', 'd', 'e']

    def test_list_input(self):
        # 空串元素被过滤；非空元素去空白
        assert nz.labels_list({'labels': ['x', ' y ', '']}) == ['x', 'y']

    def test_none_and_empty(self):
        assert nz.labels_list({}) == []
        assert nz.labels_list({'labels': None}) == []
        assert nz.labels_list({'labels': ''}) == []


# ---------------------------------------------------------------------------
# normalize_bug：集成钩子（派生字段 + reopen_count 容错 + duplicate 标记）
# ---------------------------------------------------------------------------
class TestNormalizeBug:
    def test_derives_all_std_fields(self):
        bug = nz.normalize_bug({
            'bug_id': 'BUG-1', 'status': '已关闭', 'severity': '1',
            'priority': 'P0', 'resolution': 'fixed', 'root_cause': '代码逻辑',
            'repro': '20%', 'created_at': '2026-09-01 10:00:00',
        })
        assert bug['status_group'] == 'closed'
        assert bug['severity_std'] == '致命'
        assert bug['priority_std'] == 'P0'
        assert bug['resolution_std'] == '已修复'
        assert bug['root_cause_std'] == '代码逻辑'
        assert bug['is_occasional'] is True
        assert bug['created_at_dt'] == datetime(2026, 9, 1, 10, 0, 0)

    def test_reopen_count_coercion(self):
        assert nz.normalize_bug({'reopen_count': '3'})['reopen_count'] == 3
        assert nz.normalize_bug({'reopen_count': 'abc'})['reopen_count'] == 0
        assert nz.normalize_bug({})['reopen_count'] == 0

    def test_is_duplicate_flag(self):
        # resolution_std == '重复' 或 duplicate_of 非空 → True
        assert nz.normalize_bug({'resolution': 'duplicate'})['is_duplicate_flag'] is True
        assert nz.normalize_bug({'duplicate_of': 'BUG-2'})['is_duplicate_flag'] is True
        assert nz.normalize_bug({'resolution': 'fixed'})['is_duplicate_flag'] is False

    def test_all_datetime_fields_parsed(self):
        bug = nz.normalize_bug({
            'created_at': '2026-09-01 10:00:00', 'assigned_at': '2026-09-01 11:00:00',
            'resolved_at': '2026-09-02 10:00:00', 'closed_at': '2026-09-03 10:00:00',
        })
        assert bug['created_at_dt'] == datetime(2026, 9, 1, 10, 0, 0)
        assert bug['assigned_at_dt'] == datetime(2026, 9, 1, 11, 0, 0)
        assert bug['resolved_at_dt'] == datetime(2026, 9, 2, 10, 0, 0)
        assert bug['closed_at_dt'] == datetime(2026, 9, 3, 10, 0, 0)

    def test_in_place_mutation_returns_same_dict(self):
        bug = {'bug_id': 'X'}
        assert nz.normalize_bug(bug) is bug
