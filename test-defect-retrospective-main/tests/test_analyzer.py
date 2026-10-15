# -*- coding: utf-8 -*-
"""analyzer.py 纯函数测试：百分比/均值/分桶/用例状态归一、XMind 树解析（Zen JSON
与 XMind8 XML 双格式）、parse_xmind 集成（叶子=用例、模块路径推导）。

这些函数是指标计算的基元：分母为 0、负向时长、分桶边界、用例状态归一回退
都直接影响「解决率/修复时效/通过率/用例覆盖率」的口径正确性。
XMind 双格式解析的回归风险在于格式分支选择与 detached 主题跳过逻辑。
"""
import io
import json
import zipfile
from datetime import datetime

import pytest

import analyzer as az


# ---------------------------------------------------------------------------
# _pct：百分比 + 分母为 0 → None
# ---------------------------------------------------------------------------
class TestPct:
    def test_normal(self):
        assert az._pct(1, 4) == 25.0
        assert az._pct(3, 7) == round(3 / 7 * 100, 2)

    def test_zero_denominator(self):
        assert az._pct(5, 0) is None

    def test_zero_numerator(self):
        assert az._pct(0, 5) == 0.0


# ---------------------------------------------------------------------------
# _mean_hours：剔除负值/缺失，空集 → None
# ---------------------------------------------------------------------------
class TestMeanHours:
    def test_normal_average(self):
        pairs = [
            (datetime(2026, 9, 2, 10), datetime(2026, 9, 1, 10)),   # 24h
            (datetime(2026, 9, 1, 13), datetime(2026, 9, 1, 10)),   # 3h
        ]
        assert az._mean_hours(pairs) == round((24 + 3) / 2, 1)

    def test_negative_durations_filtered(self):
        # 结束早于开始（负向时长）应被剔除
        pairs = [
            (datetime(2026, 9, 1, 9), datetime(2026, 9, 1, 10)),   # -1h 过滤
            (datetime(2026, 9, 1, 13), datetime(2026, 9, 1, 10)),  # 3h 保留
        ]
        assert az._mean_hours(pairs) == 3.0

    def test_missing_datetimes_filtered(self):
        pairs = [(None, datetime(2026, 9, 1, 10)), (datetime(2026, 9, 1, 13), None)]
        assert az._mean_hours(pairs) is None

    def test_empty(self):
        assert az._mean_hours([]) is None


# ---------------------------------------------------------------------------
# _bucket：生命周期耗时分桶边界
# ---------------------------------------------------------------------------
class TestBucket:
    def test_below_4h(self):
        assert az._bucket(0) == '＜4小时'
        assert az._bucket(3.99) == '＜4小时'

    def test_4_to_24h(self):
        assert az._bucket(4) == '4-24小时'
        assert az._bucket(23.99) == '4-24小时'

    def test_1_to_3_days(self):
        assert az._bucket(24) == '1-3天'
        assert az._bucket(71.99) == '1-3天'

    def test_above_3_days(self):
        assert az._bucket(72) == '＞3天'
        assert az._bucket(1000) == '＞3天'

    def test_none_unknown(self):
        assert az._bucket(None) == '未知'


# ---------------------------------------------------------------------------
# _tc_status_std：测试用例执行状态归一 + 默认未执行
# ---------------------------------------------------------------------------
class TestTcStatusStd:
    @pytest.mark.parametrize('raw,expected', [
        ('通过', '通过'), ('pass', '通过'), ('passed', '通过'),
        ('失败', '失败'), ('fail', '失败'), ('不通过', '失败'),
        ('阻塞', '阻塞'), ('blocked', '阻塞'),
        ('跳过', '跳过'), ('skipped', '跳过'),
        ('未执行', '未执行'), ('not run', '未执行'),
    ])
    def test_known_statuses(self, raw, expected):
        assert az._tc_status_std(raw) == expected

    def test_empty_defaults_to_not_run(self):
        assert az._tc_status_std('') == '未执行'
        assert az._tc_status_std(None) == '未执行'

    def test_unknown_defaults_to_not_run(self):
        assert az._tc_status_std('随便写的') == '未执行'


# ---------------------------------------------------------------------------
# _xmind_priority_zen / _xmind_tree_zen：JSON 树解析
# ---------------------------------------------------------------------------
class TestXmindZen:
    def test_priority_from_marker(self):
        topic = {'title': '用例A', 'markers': [{'markerId': 'priority-1'}]}
        assert az._xmind_priority_zen(topic) == 'P0'

    def test_priority_5_and_6_collapse_to_p3(self):
        for mid in ('priority-5', 'priority-6'):
            assert az._xmind_priority_zen({'markers': [{'markerId': mid}]}) == 'P3'

    def test_no_priority_returns_none(self):
        assert az._xmind_priority_zen({'title': 'X'}) is None
        assert az._xmind_priority_zen({'title': 'X', 'markers': []}) is None

    def test_tree_built_with_children(self):
        topic = {
            'title': '中心',
            'children': {'attached': [
                {'title': '模块1', 'children': {'attached': [
                    {'title': '用例A', 'markers': [{'markerId': 'priority-1'}]}
                ]}},
            ]},
        }
        tree = az._xmind_tree_zen(topic)
        assert tree['title'] == '中心'
        assert len(tree['children']) == 1
        leaf = tree['children'][0]['children'][0]
        assert leaf['title'] == '用例A'
        assert leaf['priority'] == 'P0'

    def test_empty_children(self):
        tree = az._xmind_tree_zen({'title': '叶子', 'children': {}})
        assert tree['children'] == []


# ---------------------------------------------------------------------------
# _xmind_tree_xml：XMind8 XML 树解析（detached 主题跳过）
# ---------------------------------------------------------------------------
class TestXmindXml:
    def test_parse_simple_tree(self):
        # XMind8 结构：<topic> 直挂 <title>；子主题在
        # <children><topics type="attached"><topic> 内
        xml = '''<root><topic>
            <title>中心</title>
            <children>
              <topics type="attached">
                <topic><title>用例A</title>
                  <marker-refs><marker-ref marker-id="priority-2"/></marker-refs>
                </topic>
              </topics>
            </children>
        </topic></root>'''
        tree = az._xmind_tree_xml(xml)
        assert tree['title'] == '中心'
        assert tree['children'][0]['title'] == '用例A'
        assert tree['children'][0]['priority'] == 'P1'

    def test_detached_topics_skipped(self):
        # type="detached" 的 <topics> 块不进入子节点
        xml = '''<root><topic><title>中心</title>
          <children>
            <topics type="detached">
              <topic><title>游离</title></topic>
            </topics>
          </children>
          <children>
            <topics type="attached">
              <topic><title>正常</title></topic>
            </topics>
          </children>
        </topic></root>'''
        tree = az._xmind_tree_xml(xml)
        titles = [c['title'] for c in tree['children']]
        assert '正常' in titles
        assert '游离' not in titles

    def test_no_topic_returns_empty(self):
        tree = az._xmind_tree_xml('<root></root>')
        assert tree == {'title': '', 'priority': None, 'children': []}


# ---------------------------------------------------------------------------
# parse_xmind：集成（content.json / content.xml 双格式、模块路径、叶子=用例）
# ---------------------------------------------------------------------------
def _make_xmind(path, entries):
    """构造 .xmind zip：entries 为 {name: bytes_or_str}。"""
    with zipfile.ZipFile(str(path), 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            if isinstance(data, str):
                data = data.encode('utf-8')
            z.writestr(name, data)
    return str(path)


class TestParseXmind:
    def test_content_json_leaves_become_cases(self, tmp_path):
        data = [{
            'rootTopic': {
                'title': '套件',
                'children': {'attached': [
                    {'title': '订单模块', 'children': {'attached': [
                        {'title': '下单', 'markers': [{'markerId': 'priority-1'}]},
                        {'title': '退款'},
                    ]}},
                ]},
            }
        }]
        path = _make_xmind(tmp_path / 'a.xmind', {'content.json': json.dumps(data)})
        cases = az.parse_xmind(path)
        assert len(cases) == 2
        assert cases[0]['title'] == '下单'
        assert cases[0]['priority'] == 'P0'
        assert cases[0]['module'] == '订单模块'
        assert cases[0]['status_std'] == '未执行'
        assert cases[0]['case_id'] == 'XM-001'
        assert cases[1]['module'] == '订单模块'

    def test_content_xml_format(self, tmp_path):
        xml = '''<root><topic><title>套件</title>
          <children>
            <topics type="attached">
              <topic><title>模块A</title>
                <children>
                  <topics type="attached">
                    <topic><title>用例1</title></topic>
                  </topics>
                </children>
              </topic>
            </topics>
          </children>
        </topic></root>'''
        path = _make_xmind(tmp_path / 'b.xmind', {'content.xml': xml})
        cases = az.parse_xmind(path)
        assert len(cases) == 1
        assert cases[0]['title'] == '用例1'
        assert cases[0]['module'] == '模块A'

    def test_leaf_directly_under_root_uses_root_as_module(self, tmp_path):
        # 叶子直挂中心主题时，模块回退为中心主题名
        data = [{'rootTopic': {'title': '我的套件',
                'children': {'attached': [{'title': '顶层用例'}]}}}]
        path = _make_xmind(tmp_path / 'c.xmind', {'content.json': json.dumps(data)})
        cases = az.parse_xmind(path)
        assert len(cases) == 1
        assert cases[0]['module'] == '我的套件'

    def test_invalid_structure_raises(self, tmp_path):
        path = _make_xmind(tmp_path / 'bad.xmind', {'unrelated.txt': 'x'})
        with pytest.raises(RuntimeError, match='无法识别的 xmind 结构'):
            az.parse_xmind(path)


# ---------------------------------------------------------------------------
# load_team_roles：人员角色 CSV 读取（多表头别名 / 缺文件返回 None）
# ---------------------------------------------------------------------------
class TestLoadTeamRoles:
    def test_reads_roles(self, tmp_path):
        (tmp_path / '人员角色.csv').write_text(
            '姓名,角色\n张三,前端\n李四,测试\n', encoding='utf-8')
        roles = az.load_team_roles(str(tmp_path))
        assert roles == {'张三': '前端', '李四': '测试'}

    def test_missing_file_returns_none(self, tmp_path):
        assert az.load_team_roles(str(tmp_path)) is None
