# -*- coding: utf-8 -*-
"""analyzer.py 指标计算测试。

analyzer.py 是全量指标计算的核心，包含纯函数（百分比/均值/分桶/分组统计）、
测试用例状态标准化、XMind 解析、人员角色标准化，以及最复杂的重复缺陷聚类
（并查集 + 相似度）。本测试重点覆盖：
- 纯函数边界（分母为 0、负值剔除、分桶临界点）；
- 重复缺陷聚类：平台标记、相似度聚簇、超大规模降级；
- 人员角色标准化与名单缺失/未登记分支；
- 测试用例状态标准化与 XMind 优先级映射。
"""
import os
from datetime import datetime

import pytest

import analyzer as az
from analyzer import Analyzer, HOUR_BUCKETS
import normalize as nz


# ---------------------------------------------------------------------------
# 纯函数工具
# ---------------------------------------------------------------------------

class TestPureHelpers:
    def test_pct_zero_denominator(self):
        assert az._pct(1, 0) is None

    def test_pct_rounds_to_two(self):
        assert az._pct(1, 3) == 33.33

    def test_mean_hours_excludes_negative_and_missing(self):
        pairs = [
            (datetime(2026, 1, 1, 12), datetime(2026, 1, 1, 10)),   # 2h
            (datetime(2026, 1, 1, 10), datetime(2026, 1, 1, 12)),   # 负值 → 剔除
            (None, datetime(2026, 1, 1, 10)),                        # 缺失 → 剔除
            (datetime(2026, 1, 1, 16), datetime(2026, 1, 1, 10)),   # 6h
        ]
        assert az._mean_hours(pairs) == 4.0   # (2 + 6) / 2

    def test_mean_hours_empty(self):
        assert az._mean_hours([]) is None

    @pytest.mark.parametrize('hours,expected', [
        (0, '＜4小时'),
        (3.99, '＜4小时'),
        (4, '4-24小时'),
        (24, '1-3天'),
        (71.99, '1-3天'),
        (72, '＞3天'),
        (None, '未知'),
    ])
    def test_bucket_boundaries(self, hours, expected):
        assert az._bucket(hours) == expected

    def test_group_counts_with_order(self):
        items = [{'s': '严重'}, {'s': '致命'}, {'s': '一般'}, {'s': '致命'}]
        rows = az._group_counts(items, 's', order=['致命', '严重', '一般'])
        names = [r['名称'] for r in rows]
        assert names == ['致命', '严重', '一般']
        assert rows[0]['数量'] == 2
        assert rows[0]['占比(%)'] == 50.0

    def test_group_counts_missing_value(self):
        items = [{'s': 'a'}, {'s': None}, {}]
        rows = az._group_counts(items, 's')
        # None/缺失 → '未知'
        unknown = next(r for r in rows if r['名称'] == '未知')
        assert unknown['数量'] == 2


# ---------------------------------------------------------------------------
# 测试用例状态标准化
# ---------------------------------------------------------------------------

class TestTcStatusStd:
    @pytest.mark.parametrize('raw,expected', [
        ('通过', '通过'),
        ('pass', '通过'),
        ('失败', '失败'),
        ('阻塞', '阻塞'),
        ('跳过', '跳过'),
        ('未执行', '未执行'),
        (None, '未执行'),
        ('', '未执行'),
        ('未知状态', '未执行'),
    ])
    def test_status_mapping(self, raw, expected):
        assert az._tc_status_std(raw) == expected


# ---------------------------------------------------------------------------
# XMind 优先级
# ---------------------------------------------------------------------------

class TestXmindPriority:
    @pytest.mark.parametrize('mid,expected', [
        ('priority-1', 'P0'),
        ('priority-2', 'P1'),
        ('priority-3', 'P2'),
        ('priority-4', 'P3'),
        ('priority-5', 'P3'),
        ('priority-6', 'P3'),
        ('other', None),
    ])
    def test_priority_marker(self, mid, expected):
        assert az._xmind_priority_zen({'markers': [{'markerId': mid}]}) == expected

    def test_no_markers(self):
        assert az._xmind_priority_zen({}) is None
        assert az._xmind_priority_zen({'markers': []}) is None

    def test_xmind_tree_zen(self):
        topic = {
            'title': ' 套件 ',
            'markers': [],
            'children': {'attached': [
                {'title': '模块A', 'markers': [], 'children': {'attached': [
                    {'title': '用例1', 'markers': [{'markerId': 'priority-1'}], 'children': {}},
                ]}},
            ]},
        }
        tree = az._xmind_tree_zen(topic)
        assert tree['title'] == '套件'
        assert tree['children'][0]['title'] == '模块A'
        assert tree['children'][0]['children'][0]['priority'] == 'P0'


# ---------------------------------------------------------------------------
# XMind 解析端到端（构造 zip）
# ---------------------------------------------------------------------------

class TestParseXmind:
    def _make_xmind_zip(self, tmp_path, content_json):
        import json
        import zipfile
        p = tmp_path / 'tc.xmind'
        with zipfile.ZipFile(str(p), 'w') as z:
            z.writestr('content.json', json.dumps(content_json))
        return str(p)

    def test_parse_zen_xmind(self, tmp_path):
        content = [{
            'rootTopic': {
                'title': '回归套件',
                'children': {'attached': [
                    {'title': '登录模块', 'children': {'attached': [
                        {'title': '正常登录', 'markers': [{'markerId': 'priority-1'}]},
                        {'title': '异常登录', 'markers': [{'markerId': 'priority-2'}]},
                    ]}},
                ]},
            }
        }]
        path = self._make_xmind_zip(tmp_path, content)
        cases = az.parse_xmind(path)
        assert len(cases) == 2
        assert cases[0]['title'] == '正常登录'
        assert cases[0]['priority'] == 'P0'
        assert cases[0]['module'] == '登录模块'
        assert cases[0]['status_std'] == '未执行'
        assert cases[1]['priority'] == 'P1'

    def test_parse_xmind_no_content_raises(self, tmp_path):
        import zipfile
        p = tmp_path / 'bad.xmind'
        with zipfile.ZipFile(str(p), 'w') as z:
            z.writestr('other.xml', '<x/>')
        with pytest.raises(RuntimeError, match='无法识别的 xmind 结构'):
            az.parse_xmind(str(p))


# ---------------------------------------------------------------------------
# 人员角色加载
# ---------------------------------------------------------------------------

class TestLoadTeamRoles:
    def test_loads_name_role_csv(self, tmp_path):
        p = tmp_path / '人员角色.csv'
        p.write_text('姓名,角色\n张三,测试\n李四,前端\n', encoding='utf-8')
        roles = az.load_team_roles(str(tmp_path))
        assert roles == {'张三': '测试', '李四': '前端'}

    def test_missing_file_returns_none(self, tmp_path):
        assert az.load_team_roles(str(tmp_path)) is None

    def test_alternative_column_names(self, tmp_path):
        p = tmp_path / '人员角色.csv'
        p.write_text('人员,分工\n王五,后端\n', encoding='utf-8')
        roles = az.load_team_roles(str(tmp_path))
        assert roles == {'王五': '后端'}


# ---------------------------------------------------------------------------
# Analyzer._role 人员角色标准化
# ---------------------------------------------------------------------------

class TestAnalyzerRole:
    def _make(self, team_roles=None):
        return Analyzer({'bugs': []}, team_roles=team_roles)

    def test_no_list_returns_no_list(self):
        a = self._make()
        assert a._role('张三') == '__no_list__'

    def test_unregistered_returns_none(self):
        a = self._make({'李四': '测试'})
        assert a._role('张三') is None

    @pytest.mark.parametrize('raw,expected', [
        ('测试', '测试'),
        ('qa', '测试'),
        ('前端', '前端'),
        ('fe', '前端'),
        ('后端', '后端'),
        ('be', '后端'),
        ('产品', '产品'),
        ('pm', '产品'),
    ])
    def test_role_aliases(self, raw, expected):
        a = self._make({'张三': raw})
        assert a._role('张三') == expected

    def test_unknown_role_prefixed(self):
        a = self._make({'张三': '运维'})
        assert a._role('张三') == '其他:运维'


# ---------------------------------------------------------------------------
# 重复缺陷聚类（并查集 + 相似度）
# ---------------------------------------------------------------------------

def _bug(bug_id, title, module='订单', severity='一般', status='closed',
         resolution='已修复', dup_flag=False, created_dt=None):
    # 设置原始字段，由 normalize_bug 派生 std 字段，保证测试贴近真实数据流
    b = {
        'bug_id': bug_id, 'title': title, 'module': module,
        'severity': severity, 'status': status, 'resolution': resolution,
        'created_at': (created_dt or datetime(2026, 8, 1)).strftime('%Y-%m-%d %H:%M:%S'),
        'reopen_count': 0,
    }
    if dup_flag:
        b['resolution'] = '重复'          # 触发 is_duplicate_flag
    return nz.normalize_bug(b)


class TestFindDuplicates:
    def test_platform_flagged_counted(self):
        valid = [_bug('A', '标题A', dup_flag=True), _bug('B', '标题B')]
        a = Analyzer({'bugs': valid})
        dup_idx, groups = a._find_duplicates(valid)
        assert 0 in dup_idx   # A 被平台标记
        assert 1 not in dup_idx

    def test_similarity_clusters_same_module_severity(self):
        # 同模块同严重度，标题高度相似 → 聚簇
        b1 = _bug('A', '登录失败提示错误信息', module='登录', severity='严重')
        b2 = _bug('B', '登录失败提示错误', module='登录', severity='严重')
        a = Analyzer({'bugs': [b1, b2]})
        dup_idx, groups = a._find_duplicates([b1, b2])
        assert len(dup_idx) == 2
        assert len(groups) == 1
        assert groups[0]['数量'] == 2

    def test_different_module_not_duplicated(self):
        b1 = _bug('A', '登录失败提示错误', module='登录', severity='严重')
        b2 = _bug('B', '登录失败提示错误', module='订单', severity='严重')
        a = Analyzer({'bugs': [b1, b2]})
        dup_idx, _ = a._find_duplicates([b1, b2])
        assert len(dup_idx) == 0

    def test_different_severity_not_duplicated(self):
        b1 = _bug('A', '登录失败提示错误', module='登录', severity='严重')
        b2 = _bug('B', '登录失败提示错误', module='登录', severity='一般')
        a = Analyzer({'bugs': [b1, b2]})
        dup_idx, _ = a._find_duplicates([b1, b2])
        assert len(dup_idx) == 0

    def test_low_similarity_not_duplicated(self):
        b1 = _bug('A', '登录失败', module='登录', severity='严重')
        b2 = _bug('B', '下单成功', module='登录', severity='严重')
        a = Analyzer({'bugs': [b1, b2]})
        dup_idx, _ = a._find_duplicates([b1, b2])
        assert len(dup_idx) == 0

    def test_large_dataset_degrades_to_platform_only(self):
        # 构造 >3000 条，相似度比对降级，仅平台标记计入
        many = [_bug('X%d' % i, '完全不同的标题%d' % i, module='m%d' % (i % 50),
                     severity='一般') for i in range(3001)]
        many[0]['is_duplicate_flag'] = True
        a = Analyzer({'bugs': many})
        dup_idx, groups = a._find_duplicates(many)
        assert 0 in dup_idx
        assert len(dup_idx) == 1
        assert groups == []
        assert any('超过3000' in m for m in a.missing)


# ---------------------------------------------------------------------------
# overview 大盘指标
# ---------------------------------------------------------------------------

class TestOverview:
    def test_invalid_bugs_excluded_from_valid(self):
        invalid = _bug('X', '无效', dup_flag=False)
        invalid['resolution_std'] = '无效'
        invalid['is_duplicate_flag'] = False
        valid_bug = _bug('A', '有效')
        bugs = [invalid, valid_bug]
        a = Analyzer({'bugs': bugs}, release_time=datetime(2026, 9, 1))
        overview, valid = a.overview()
        assert overview['bug_total'] == 2
        assert overview['invalid_total'] == 1
        assert overview['valid_total'] == 1

    def test_resolved_rate_is_closed_over_valid(self):
        closed = _bug('A', '已关闭')
        closed['status_group'] = 'closed'
        open_bug = _bug('B', '打开中')
        open_bug['status_group'] = 'open'
        a = Analyzer({'bugs': [closed, open_bug]}, release_time=datetime(2026, 9, 1))
        overview, _ = a.overview()
        assert overview['closed_total'] == 1
        assert overview['resolved_rate(%)'] == 50.0
