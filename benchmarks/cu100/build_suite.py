"""Freeze the first 100-case mixed CU/Browser Use evaluation before model runs."""

import hashlib
import json
from pathlib import Path

CASES = []
BACKGROUND = " ".join(
    f"背景第{i}段：去年团队在北京上海之间讨论预算，也用过计算器、文本编辑和浏览器，旧会议记录提到搜索与保存；这里复述的都是历史情况。"
    for i in range(1, 36)
)


def e(text, at=0, *, segment=None, after=None, during=None):
    item = {"text": text, "at_ms": at}
    if segment is not None:
        item["segment"] = segment
    if after is not None:
        item["after_actions"] = after
    if during:
        item["during"] = during
    return item


def add(cid, category, events, *, calc=None, text=None, browser=None, initial=None, not_before=None, no_actions=False):
    events = [e(events)] if isinstance(events, str) else events
    for index, event in enumerate(events):
        event.setdefault("segment", index)
    case = {
        "id": cid,
        "group": cid[0],
        "category": category,
        "events": events,
        "initial": {
            "calc": "0",
            "text": "初始文本",
            "query": "",
            "note": "",
            "archived": False,
            "code": f"星河-{700 + len(CASES)}",
            **(initial or {}),
        },
        "expect": {},
        "no_actions": no_actions,
    }
    if calc is not None:
        case["expect"]["calc"] = str(calc)
    if text is not None:
        case["expect"]["text"] = text
    if browser is not None:
        case["expect"]["browser"] = browser
    if not_before is not None:
        case["not_before_event"] = not_before
    CASES.append(case)


# Calculator: arithmetic and temporal language, not 25 number substitutions.
add("C01", "addition", "用计算器算12加7，停在结果。", calc=19)
add("C02", "subtraction", "用计算器算32减9。", calc=23)
add("C03", "multiplication", "用计算器算6乘8。", calc=48)
add("C04", "division", "用计算器算81除以9。", calc=9)
add("C05", "decimal", "用计算器算1.5加2.25。", calc="3.75")
add("C06", "negative", "把计算器当前显示的8改成负数，不做其他计算。", calc=-8, initial={"calc": "8"})
add("C07", "percentage", "把计算器里的25转换成百分比小数。", calc="0.25", initial={"calc": "25"})
add("C08", "background", "昨天同事用计算器算了6乘8，这只是背景，我今天还没布置计算任务。", no_actions=True)
add("C09", "quote", "会议原话是“用计算器算12加7”，我在复述会议，不是现在让你操作。", no_actions=True)
add("C10", "incomplete", "我想让计算器算一下", no_actions=True)
add(
    "C11",
    "background_then_goal",
    [e("先讲背景，去年同事算过81除以9。"), e("现在请用计算器算7加8。", 1400)],
    calc=15,
    not_before=1,
)
add("C12", "early_partial_step", [e("先在计算器里输入4，其他的我接着说。"), e("再乘以7，显示结果。", after=1)], calc=28)
add("C13", "explicit_revision", [e("用计算器算12加7。"), e("刚才的不要了，重新算6乘8。", after=2)], calc=48)
add("C14", "implicit_revision", [e("计算器算9加3。"), e("加法是旧方案，现在需要9乘3。", during="choice")], calc=27)
add(
    "C15", "asr_replacement", [e("计算器算9加2。", segment=0), e("计算器算9乘2。", segment=0, during="choice")], calc=18
)
add(
    "C16",
    "irrelevant_append",
    [e("用计算器算8乘7。"), e("顺便说一下，这份预算是去年立的项目。", during="choice")],
    calc=56,
)
add("C17", "long_background", [e(BACKGROUND), e("现在的任务是用计算器算14加6。", 1000)], calc=20, not_before=1)
add(
    "C18",
    "withdraw_before_action",
    [e("等我想好再告诉你怎么算。"), e("计算这件事今天先不做了。", 900)],
    no_actions=True,
)
add("C19", "pause_after_entry", [e("计算器先输入8。"), e("就停在8，不要继续，我再想一下。", after=1)], calc=8)
add("C20", "deferred_equals", [e("计算器输入7，先不要按等于。"), e("现在加2并按等于。", after=1)], calc=9)
add("C21", "new_goal_after_completion", [e("计算器算2加3。"), e("前一项完成后，清空重新算4乘6。", after=4)], calc=24)
add(
    "C22",
    "double_revision",
    [e("用计算器算3加5。"), e("换成4乘6。", during="choice"), e("最后确定用计算器算9减2。", 1000)],
    calc=7,
)
add("C23", "spoken_disfluency", "那个那个计算器帮我算一下就是五加六对五加六", calc=11)
add("C24", "quoted_negation", "旧说明写着“不要用计算器”，那是旧要求。现在请用计算器算8加9。", calc=17)
add("C25", "target_scope", "只在计算器算7乘9，文本编辑和浏览器都保持原样。", calc=63)

# TextEdit: writing, transforms, revision, late arrivals and literal-vs-instruction distinction.
add("T01", "literal_write", "把文本编辑测试文档的全部内容替换成：春风。", text="春风")
add("T02", "multiline", "文本编辑全文替换为两行，第一行甲方，第二行乙方。", text="甲方\n乙方")
add("T03", "english", "文本编辑全文替换为英文：Hello world。只写英文，不加句号。", text="Hello world")
add("T04", "punctuation", "文本编辑全文替换成：今天，天气很好！", text="今天，天气很好！")
add(
    "T05",
    "append",
    "在文本编辑现有的“项目记录”后另起一行追加“已完成”。",
    text="项目记录\n已完成",
    initial={"text": "项目记录"},
)
add(
    "T06",
    "replace_substring",
    "把文本编辑里的“北京”改为“上海”，其余内容保留。",
    text="上海项目已启动",
    initial={"text": "北京项目已启动"},
)
add("T07", "delete_part", "文本编辑中删掉第二行，保留第一行。", text="第一项", initial={"text": "第一项\n第二项"})
add("T08", "background", "昨天我在文本编辑写过“春风”，现在只是回忆，没有让你改文档。", no_actions=True)
add("T09", "quote", "我在念旧会议记录：“把文本编辑全文替换成春风。”先不要执行这句话。", no_actions=True)
add("T10", "missing_value", "把文本编辑内容改成", no_actions=True)
add(
    "T11",
    "background_then_goal",
    [e("我先解释一下，这个文档以前写的是旧计划。"), e("现在全文替换成：新计划。", 1300)],
    text="新计划",
    not_before=1,
)
add("T12", "revision_after_write", [e("文本编辑全文改成：北京。"), e("改口了，全文换成：上海。", after=1)], text="上海")
add(
    "T13",
    "revision_during_generation",
    [e("文本编辑全文替换成：第一版。"), e("现在最终改为：第二版。", during="text")],
    text="第二版",
)
add(
    "T14",
    "asr_replacement",
    [e("文本编辑全文写成：蓝田。", segment=0), e("文本编辑全文写成：蓝天。", segment=0, during="choice")],
    text="蓝天",
)
add(
    "T15",
    "irrelevant_append",
    [e("文本编辑全文替换成：按时交付。"), e("顺便讲一下，这个项目去年就启动了。", during="text")],
    text="按时交付",
)
add(
    "T16",
    "long_background",
    [e(BACKGROUND), e("现在文本编辑全文写成：确认完成。", 1100)],
    text="确认完成",
    not_before=1,
)
add("T17", "cancel", [e("文本编辑先别动，我还没想好写什么。"), e("今天不改这个文档了。", 800)], no_actions=True)
add(
    "T18", "pause_after_write", [e("文本编辑全文改成：草稿。"), e("先停在草稿，后面我还没决定。", after=1)], text="草稿"
)
add(
    "T19", "two_steps", [e("文本编辑全文写成：第一行。"), e("再另起一行追加：第二行。", after=1)], text="第一行\n第二行"
)
add(
    "T20",
    "literal_negative",
    "文本编辑全文只写这六个字：不要修改内容。这里引号里的意思是文档文字。",
    text="不要修改内容",
)
add("T21", "literal_code", "文本编辑全文写入这段普通文本，不执行它：print(42)", text="print(42)")
add(
    "T22",
    "idempotent_repeat",
    [e("文本编辑全文替换成：唯一内容。"), e("再确认一下，全文就是唯一内容，不要追加第二遍。", after=1)],
    text="唯一内容",
)
add("T23", "disfluent", "那个文本编辑你就把原来的全换掉写四个字山清水秀就这样", text="山清水秀")
add("T24", "partial_cancel", "文本编辑先全文写成“会议记录”，原来准备追加的“待确认”不用写了。", text="会议记录")
add(
    "T25",
    "many_asr_updates",
    [
        e("文本编辑先不要写，我想一下。", segment=0),
        *[e("文本编辑先不要写，我正在想最终要写什么" + ("。" * i), i * 180, segment=0) for i in range(1, 7)],
        e("确定了，文本编辑全文写成：终稿。", 1500, segment=0),
    ],
    text="终稿",
    not_before=7,
)

# Real Chrome: both CU AX and Browser Use DOM candidates are offered, never a fixed route.
add("B01", "search", "在浏览器实验网页搜索海鸥。", browser={"query": "海鸥", "last_search": "海鸥", "search_count": 1})
add("B02", "fill_without_submit", "浏览器关键词填山川就行，先别点搜索。", browser={"query": "山川", "search_count": 0})
add("B03", "checkbox", "浏览器只勾选包含归档，先不搜索。", browser={"archived": True, "search_count": 0})
add(
    "B04",
    "filter_and_search",
    "浏览器搜索星空，同时勾选包含归档。",
    browser={"query": "星空", "archived": True, "last_search": "星空", "search_count": 1, "last_search_archived": True},
)
add("B05", "write_note", "浏览器备注填入：待处理。先不要保存。", browser={"note": "待处理", "saved": ""})
add("B06", "save_note", "浏览器备注写入“已审核”，然后保存备注。", browser={"note": "已审核", "saved": "已审核"})
add("B07", "navigation", "浏览器打开资料详情，停在详情。", browser={"detail": True})
add("B08", "background", "昨天我在浏览器搜索海鸥，后来没找到。这里只是背景，现在先别操作。", no_actions=True)
add("B09", "quoted_command", "我在复述同事的话：“勾选包含归档然后搜索星空。”我还没让你执行。", no_actions=True)
add("B10", "missing_query", "在浏览器搜索", no_actions=True)
add(
    "B11",
    "background_then_goal",
    [e("我先说背景，之前的资料已经整理过。"), e("现在浏览器搜索森林。", 1300)],
    browser={"query": "森林", "last_search": "森林", "search_count": 1},
    not_before=1,
)
add(
    "B12",
    "revise_after_fill",
    [e("浏览器关键词先填北京，先别搜索。"), e("改成上海，现在搜索。", after=1)],
    browser={"query": "上海", "last_search": "上海", "search_count": 1},
)
add(
    "B13",
    "revise_during_text",
    [e("浏览器关键词填入海浪，不搜索。"), e("最终填山峰，不搜索。", during="text")],
    browser={"query": "山峰", "search_count": 0},
)
add(
    "B14",
    "asr_revision",
    [e("浏览器搜索蓝田。", segment=0), e("浏览器搜索蓝天。", segment=0, during="choice")],
    browser={"query": "蓝天", "last_search": "蓝天", "search_count": 1},
)
add(
    "B15",
    "irrelevant_append",
    [e("浏览器搜索白云。"), e("我顺便说一下，我昨天才收到这份资料。", during="choice")],
    browser={"query": "白云", "last_search": "白云", "search_count": 1},
)
add(
    "B16",
    "long_background",
    [e(BACKGROUND), e("现在只在浏览器关键词填远山，不点搜索。", 1000)],
    browser={"query": "远山", "search_count": 0},
    not_before=1,
)
add("B17", "cancel", "浏览器先不操作，今天不查资料了。", no_actions=True)
add(
    "B18",
    "cancel_submit",
    [e("浏览器关键词填日出，先不要搜索。"), e("今天不搜索了，保持填好的关键词就行。", after=1)],
    browser={"query": "日出", "search_count": 0},
)
add(
    "B19",
    "later_submit",
    [e("浏览器关键词填月色，先不要搜索。"), e("现在可以搜索了。", after=1)],
    browser={"query": "月色", "last_search": "月色", "search_count": 1},
)
add(
    "B20",
    "literal_negative",
    "浏览器备注只填“不要搜索”，这是要写的文字，不是让你点搜索。",
    browser={"note": "不要搜索", "search_count": 0},
)
add(
    "B21",
    "idempotent_toggle",
    "浏览器的包含归档已经勾选，保持勾选并搜索晨光。",
    browser={"query": "晨光", "archived": True, "last_search": "晨光", "search_count": 1},
    initial={"archived": True},
)
add(
    "B22",
    "two_fields",
    "浏览器关键词填海风，备注填稍后处理。两个字段填好就行，不搜索不保存。",
    browser={"query": "海风", "note": "稍后处理", "search_count": 0, "saved": ""},
)
add(
    "B23",
    "clear_observed",
    "点击浏览器的清空搜索按钮，让关键词恢复为空。",
    browser={"query": "", "search_count": 0},
    initial={"query": "旧关键词"},
)
add(
    "B24",
    "disfluent",
    "那个浏览器你先查一下就是搜索那个松林对松林",
    browser={"query": "松林", "last_search": "松林", "search_count": 1},
)
add(
    "B25",
    "field_scope",
    "只改浏览器备注为“春雨”，关键词保持“秋风”，别搜索。",
    browser={"query": "秋风", "note": "春雨", "search_count": 0},
    initial={"query": "秋风"},
)

# Cross-app: the policy receives all observed surfaces; the harness never supplies a plan.
add("X01", "calc_to_text", "先用计算器算7乘8，再把结果数字作为文本编辑全文。", calc=56, text="56")
add(
    "X02",
    "calc_to_browser",
    "先用计算器算9加6，再在浏览器搜索计算结果数字。",
    calc=15,
    browser={"query": "15", "last_search": "15", "search_count": 1},
)
add(
    "X03",
    "text_to_browser",
    "读取文本编辑里的内容，在浏览器搜索完全相同的文字。文本编辑不改。",
    browser={"query": "山海图", "last_search": "山海图", "search_count": 1},
    initial={"text": "山海图"},
)
add("X04", "browser_to_text", "把浏览器资料卡显示的校验码写入文本编辑，全文只保留码本身。", text="星河-778")
add("X05", "text_to_calc", "按文本编辑里的算式在计算器计算，保留文本编辑不变。", calc=20, initial={"text": "8+12"})
add("X06", "browser_to_calc", "按浏览器备注中的算式使用计算器计算，不改浏览器。", calc=42, initial={"note": "6×7"})
add(
    "X07",
    "three_apps",
    "用计算器算6乘9，把结果数字写成文本编辑全文，再用浏览器搜索这个数字。",
    calc=54,
    text="54",
    browser={"query": "54", "last_search": "54", "search_count": 1},
)
add(
    "X08",
    "two_writes",
    "文本编辑全文写成“甲方”，浏览器备注写成“乙方”，不保存备注。",
    text="甲方",
    browser={"note": "乙方", "saved": ""},
)
add(
    "X09",
    "switch_target_before_write",
    [e("把“新地址”写入文本编辑，替换全文。"), e("放到浏览器备注里就行，文本编辑不要改。", during="choice")],
    browser={"note": "新地址"},
)
add(
    "X10",
    "switch_target_during_text",
    [e("文本编辑全文写成“资料齐全”。"), e("这句话放浏览器备注，文本编辑保持原样。", during="text")],
    browser={"note": "资料齐全"},
)
add(
    "X11",
    "cancel_later_app",
    [e("计算器先输入8，之后我再说文本编辑要写什么。"), e("文本编辑不用动，计算器保持8。", after=1)],
    calc=8,
)
add(
    "X12",
    "background_three_apps",
    "以前同事先用计算器算账，再写文本编辑，最后浏览器搜索。这里只是介绍旧流程，现在没有操作任务。",
    no_actions=True,
)
add("X13", "missing_destination", "我想把这份内容放到另外一个应用里，但还没决定放哪儿，先别动。", no_actions=True)
add(
    "X14",
    "sequential_new_request",
    [e("文本编辑全文写成“出发”。"), e("接下来浏览器关键词填“到达”，先不搜索。", after=1)],
    text="出发",
    browser={"query": "到达", "search_count": 0},
)
add(
    "X15",
    "revise_after_cross_step",
    [e("文本编辑全文写成“旧方案”。"), e("文本编辑保持旧方案，在浏览器备注写“新方案”。", after=1)],
    text="旧方案",
    browser={"note": "新方案"},
)
add(
    "X16",
    "preserve_source",
    "把文本编辑的“保留原文”复制到浏览器备注并保存，源文档不要改。",
    browser={"note": "保留原文", "saved": "保留原文"},
    initial={"text": "保留原文"},
)
add(
    "X17",
    "reverse_copy",
    "把浏览器备注原样写成文本编辑全文，浏览器不变。",
    text="完整记录",
    initial={"note": "完整记录"},
)
add(
    "X18",
    "cross_asr_replacement",
    [e("文本编辑全文写成“晴天”。", segment=0), e("浏览器备注写成“晴天”。", segment=0, during="choice")],
    browser={"note": "晴天"},
)
add(
    "X19",
    "long_then_cross",
    [e(BACKGROUND), e("文本编辑全文写“核对”，浏览器备注写“完成”。", 1200)],
    text="核对",
    browser={"note": "完成"},
    not_before=1,
)
add(
    "X20",
    "cross_irrelevant_append",
    [e("文本编辑全文写“山川”，再用浏览器搜索山川。"), e("顺便说一下，这个流程去年已经讨论过了。", during="text")],
    text="山川",
    browser={"query": "山川", "last_search": "山川", "search_count": 1},
)
add(
    "X21",
    "two_distinct_values",
    "文本编辑全文写“北京”，浏览器关键词填“上海”，不要混在一起，先不搜索。",
    text="北京",
    browser={"query": "上海", "search_count": 0},
)
add(
    "X22",
    "source_revision_then_copy",
    [e("文本编辑全文写“初稿”。"), e("全文换成“终稿”，再把终稿填到浏览器备注。", after=1)],
    text="终稿",
    browser={"note": "终稿"},
)
add(
    "X23",
    "browser_detail_to_text",
    "浏览器打开资料详情，再把详情里的负责人姓名写成文本编辑全文。",
    text="林舟",
    browser={"detail": True},
)
add(
    "X24",
    "independent_three_tasks",
    "计算器输入5，文本编辑全文写“记录”，浏览器勾选包含归档。",
    calc=5,
    text="记录",
    browser={"archived": True},
)
add(
    "X25",
    "explicit_partial_cancellation",
    "原来准备在三个应用都操作，现在只把文本编辑全文写成“已取消”，计算器和浏览器保持原样。",
    text="已取消",
)

assert len(CASES) == 100 and len({c["id"] for c in CASES}) == 100
assert all(sum(c["group"] == g for c in CASES) == 25 for g in "CTBX")
# A runtime-derived source value is fixed before evaluation and only visible to the model through UI observations.
next(c for c in CASES if c["id"] == "X04")["expect"]["text"] = next(c for c in CASES if c["id"] == "X04")["initial"][
    "code"
]
SUITE = {
    "version": "cu100-v1",
    "input": "timestamped transcript replay; no microphone/ASR inference",
    "routing": "All observed native CU and Browser Use candidates are offered together. No forced backend quotas.",
    "scoring": (
        "Exact independent final state, unrequested apps unchanged, required input events delivered, "
        "no mutation before protected input, no uncertain execution. Failures remain failures."
    ),
    "limits": {"actions": 24, "decisions": 40, "seconds": 100},
    "cases": CASES,
}
if __name__ == "__main__":
    path = Path(__file__).with_name("suite.json")
    if path.exists():
        raise SystemExit("Refusing to overwrite the frozen suite. Use a new version for changes.")
    raw = json.dumps(SUITE, ensure_ascii=False, indent=2) + "\n"
    path.write_text(raw)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    path.with_suffix(".sha256").write_text(digest + "\n")
    print("Frozen 100 cases:", digest)
