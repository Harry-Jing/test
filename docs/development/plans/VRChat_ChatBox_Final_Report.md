# VRChat ChatBox 最终逆向报告（面向项目实现 / 交接给别人或别的 AI）

## 0. 这份报告的用途

这份报告的目标不是展示逆向过程本身，而是把目前**已经确认**、**高置信**、**足够直接落地**的信息整理成一份可交接的实现说明。

读者不需要知道 Unity、TMP、IL2CPP 或 VRChat 的背景，也应该能从这份文档直接得到：

1. VRChat ChatBox 的核心文本参数是什么。
2. 为什么之前的黑盒实验会得到 `x≈29/行`、`中≈15/行`、`最多 9 行`。
3. 如果要写一个**可靠的文本裁切 / 自动换行模拟器**，应该如何实现。
4. 哪些结论已经足够拿来做项目，哪些地方仍然保留不确定性。

---

## 1. 可直接用于项目的结论

先给可以直接复制到项目里的版本。

```yaml
vrchat_chatbox:
  purpose: "模拟 VRChat ChatBox 的长文本换行与 9 行裁切"

  text_object:
    normal_path: "VRCPlayer > NameplateContainer > ChatBubble > Canvas > Chat > ChatText"
    mirror_path: "VRCPlayer > NameplateContainer > ChatBubbleMirror > Canvas > Chat > ChatText"

  rect:
    size_px: [300, 265]
    margin_px: [10, 10, 10, 10]
    usable_size_px: [280, 245]

  tmp_text:
    font_size: 18
    character_spacing: 0
    word_spacing: 0
    line_spacing: 0
    enable_auto_sizing: false
    text_wrapping_mode: "Normal"
    horizontal_alignment: "Center"
    vertical_alignment: "Middle"

  fonts:
    primary_font_asset: "NotoSans-Regular SDF"
    primary_raw_font: "NotoSans-Regular"
    primary_raw_font_postscript: "NotoSans-Regular"
    cjk_primary_fallback: "NotoSansCJK-JP-Regular SDF"
    emoji_fallback: "NotoEmoji-Regular SDF"

  wrapping:
    width_budget_px: 280
    height_budget_px: 245
    max_lines: 9
    break_strategy: "按字形宽度累加；优先在合法断点回退；无合法断点时按 grapheme cluster 硬断"
    line_break_rules:
      - "使用 Unicode line break 思路"
      - "叠加 TMP Leading / Following Characters 规则"
      - "空格提供断点；在空格处断行时，新行开头不保留该空格（基于黑盒观察）"
      - "CJK 连续文本可逐字断，但不得让 Following Characters 落到行首"

  implementation_note:
    - "如果追求高保真，多语言必须做 shaping（如 HarfBuzz），不能只按 codepoint 宽度累加。"
    - "如果只是做 Latin/CJK 裁切器，主字体 + CJK fallback + TMP 断行表已经足够实用。"
```

这就是当前最推荐的实现基准。

---

## 2. 最重要的结论，浓缩成一句话

**VRChat 这个 build 的 ChatBox，本质上可以近似成：在一个 `300 × 265` 的 TMP 文本框里，使用 `fontSize = 18`、`margin = 10`、主字体 `NotoSans-Regular`、CJK 主要 fallback `NotoSansCJK-JP-Regular`，在 `280 px` 可用宽度内按真实字形宽度贪心换行，并裁切到最多 9 行。**

这条结论足以解释目前几乎所有关键实验现象。

---

## 3. 分析了哪些数据

这次最终结论综合了 3 类来源。

### 3.1 完整游戏文件

使用了你上传的完整游戏包，其中包括：

- `VRChat/GameAssembly.dll`
- `VRChat/VRChat_Data/il2cpp_data/Metadata/global-metadata.dat`
- `VRChat/VRChat_Data/sharedassets0.assets`
- `VRChat/VRChat_Data/resources.assets`

### 3.2 之前的纯数据包

也参考了早先的 `VRChat_Data.zip`，用于确认字体与层级对象。

### 3.3 你的黑盒实验

使用了你提供的：

- 单字符重复测试
- 标点与中文测试
- 混合文本观察
- `vrchat_chatbox_results_filled_partial.csv`
- 你补发的 `a..z`、`A..H` 单字符容量统计

这一步很重要，因为最终可用模型不是只靠字体名猜出来的，而是**资源级参数 + 黑盒现象双向校验**得出的。

---

## 4. 方法说明：这次结论是怎么得出的

### 4.1 先从 Unity 资源里找 ChatBox 文本对象

通过 `sharedassets0.assets` 的对象层级，可以确认聊天内容文本对象路径为：

- 普通气泡：`VRCPlayer > NameplateContainer > ChatBubble > Canvas > Chat > ChatText`
- 镜像气泡：`VRCPlayer > NameplateContainer > ChatBubbleMirror > Canvas > Chat > ChatText`

TypingIndicator 的文本对象是另一条路径，不是聊天内容本身。

### 4.2 再确认它实际绑定的字体和材质

`ChatText` 绑定的关键资源为：

- TMP 字体资产：`NotoSans-Regular SDF`（`sharedassets0.assets`, path id `6203`）
- ChatBox 材质：`NotoSans-Regular SDF Nameplates ChatBubble`（path id `103`）
- SDF atlas：`NotoSans-Regular SDF Atlas`（path id `494`）

同时还确认到了原始嵌入字体对象：

- `NotoSans-Regular`（`Font`, path id `925`）
- `NotoSansCJK-JP-Regular`（`Font`, path id `923`）

### 4.3 由于 metadata 仍受保护，TMP 字段采用“高置信提取”而不是完整 typetree dump

虽然这次游戏包包含了 `GameAssembly.dll`，但 `global-metadata.dat` 的文件头不是标准 IL2CPP metadata 头；它不能被现成的 typetree 生成流程直接载入。

因此，**没有拿到完整、自动化、完全可读的自定义脚本 typetree**。

但这不影响关键结论，因为本次真正需要的多数参数都来自 `TMP_Text` 基类字段。它们可以通过：

- 原始 MonoBehaviour 序列化数据
- 已知 `TMP_Text` 字段布局
- 多个文本对象的交叉校验
- 与黑盒现象的一致性

来高置信提取。

### 4.4 最后再用字体度量去解释实验值

从提取出的原始字体文件里读取：

- `unitsPerEm`
- `advance width`
- `ascent / descent / lineGap`

再结合：

- `fontSize = 18`
- `RectTransform = 300 × 265`
- `margin = (10,10,10,10)`

可以直接推出：

- `x ≈ 29/行`
- `中 ≈ 15/行`
- `最多 9 行`

这样就把“黑盒结果”变成了“资源参数可解释结果”。

---

## 5. 已确认的对象、资源与参数

### 5.1 文本对象路径

聊天内容文本对象：

- `VRCPlayer > NameplateContainer > ChatBubble > Canvas > Chat > ChatText`
- `VRCPlayer > NameplateContainer > ChatBubbleMirror > Canvas > Chat > ChatText`

TypingIndicator 文本对象：

- `VRCPlayer > NameplateContainer > ChatBubble > Canvas > TypingIndicator > Text`
- `VRCPlayer > NameplateContainer > ChatBubbleMirror > Canvas > TypingIndicator > Text`

### 5.2 ChatText 的 TMP 核心字段（高置信）

普通和镜像 `ChatText` 的主参数一致：

| 字段 | 值 |
|---|---:|
| `fontSize` | `18.0` |
| `fontSizeBase` | `18.0` |
| `fontWeight` | `400` (`Regular`) |
| `fontStyle` | `Normal` |
| `enableAutoSizing` | `false` |
| `fontSizeMin` | `16.0` |
| `fontSizeMax` | `26.0` |
| `characterSpacing` | `0.0` |
| `wordSpacing` | `0.0` |
| `lineSpacing` | `0.0` |
| `lineSpacingAdjustment` | `0.0` |
| `paragraphSpacing` | `0.0` |
| `characterWidthAdjustment` | `0.0` |
| `textWrappingMode` | `Normal` |
| `wordWrappingRatios` | `0.4` |
| `margin` | `(10, 10, 10, 10)` |
| 水平对齐 | `Center` |
| 垂直对齐 | `Middle` |

说明：

- 你之前关心的 `fontSize / characterSpacing / wordSpacing / lineSpacing / margin / enableAutoSizing`，现在都已经有高置信结果。
- `enableWordWrapping` 没有作为单独的已确认字段拿到；但文本确实在换行，且 `textWrappingMode = Normal` 已提取出来，因此项目实现中应视为**正常启用换行**。

### 5.3 ChatText 的 RectTransform

普通与镜像 `ChatText` 都是：

| 字段 | 值 |
|---|---|
| `sizeDelta` | `(300, 265)` |
| `anchorMin` | `(0.5, 0.5)` |
| `anchorMax` | `(0.5, 0.5)` |
| `anchoredPosition` | `(0, 0)` |
| `pivot` | `(0.5, 0.5)` |

结论：**聊天内容文本本身处在一个固定尺寸文本框里。**

### 5.4 Chat 容器与 Canvas

- `Chat` 本身是 stretch 到父 Canvas 的。
- `Canvas` 的本地缩放是 `(2,2,2)`，而父层级 `ChatBubble` 本地缩放是 `(0.5,0.5,0.5)`。

对项目实现而言，不需要把这两层缩放再额外引入文本宽度公式；因为真正决定换行的是 **ChatText 自己的本地矩形与 TMP 参数**。

### 5.5 TypingIndicator（补充）

TypingIndicator 使用的是同一套主字体资产，但参数不同：

- `fontSize = 40`
- `margin = (0,5,0,5)`
- `characterSpacing = 0`
- `wordSpacing = 0`
- `lineSpacing = 0`
- `enableAutoSizing = false`

它不影响聊天内容的换行模型，但如果项目里要模拟“正在输入”提示，可以单独使用这套参数。

---

## 6. 字体资源：主字体、原始字体与 fallback

### 6.1 主字体资产

聊天内容使用的主 TMP 字体资产是：

- `NotoSans-Regular SDF`（path id `6203`）

对应的原始字体对象是：

- `NotoSans-Regular`（path id `925`）

从 name table 可确认其原始字体信息：

- family: `Noto Sans`
- style: `Regular`
- full name: `Noto Sans Regular`
- PostScript: `NotoSans-Regular`
- version: `Version 2.000;GOOG;...`

这意味着：**拉丁文本的宽度行为可以直接用 `NotoSans-Regular` 的真实字形 advance 来解释。**

### 6.2 CJK fallback

在 `NotoSans-Regular SDF` 的本地 fallback 表里，最关键的 CJK 项是：

- `NotoSansCJK-JP-Regular SDF`

对应原始字体对象为：

- `NotoSansCJK-JP-Regular`（path id `923`）

这说明当前这个 ChatBox 的多语言处理不是“只靠一个 `NotoSans-Regular.ttf`”，而是**主字体 + fallback 链**。

对于中文 / 日文 / 全角标点，当前最合理的宽度模型应优先考虑 `NotoSansCJK-JP-Regular`。

### 6.3 已观测到的 fallback 链（顺序按解析结果）

在 `NotoSans-Regular SDF` 中，解析到的本地 fallback 列表为：

1. `VRCCustom SDF`
2. `NotoEmoji-Regular SDF`
3. `NotoSansCJK-JP-Regular SDF`
4. `NotoSansHebrew-Regular SDF`
5. `NotoSansArabic-Regular SDF`
6. `NotoSansThai-Regular SDF`
7. `NotoSansArmenian-Regular SDF`
8. `NotoSansBengali-Regular SDF`
9. `NotoSansDevanagari-Medium SDF`
10. `NotoSansGeorgian-Regular SDF`
11. `NotoSansGujarati-Regular SDF`
12. `NotoSansGurmukhi-Regular SDF`
13. `NotoSansKannada-Regular SDF`
14. `NotoSansLao-Regular SDF`
15. `NotoSansMalayalam-Regular SDF`
16. `NotoSansOriya-Regular SDF`
17. `NotoSansTamil-Regular SDF`
18. `NotoSansTelugu-Regular SDF`
19. `NotoSansTibetanV-Regular SDF`

注意：

- 这条 fallback 链是从 `TMP_FontAsset` 原始 PPtr 数组推断出来的，置信度高，但形式上仍属于“高置信推断”，不是完整 typetree 文本导出。
- `sharedassets0.assets` 里确实还存在 `NotoSansCJK-SC/TC/KR` 资产，但在这条本地 fallback 链里，直接观察到的 CJK 主项是 `NotoSansCJK-JP-Regular SDF`。

### 6.4 外部测试是否可以用 Google / Noto 公开字体

可以，但要区分两个目标：

- **想做快速外部测试**：可以先用公开的 `Noto Sans` + `Noto Sans CJK` 对应字体。
- **想尽量贴近当前 build**：优先使用从这个 VRChat build 中提取出来的原始字体文件。

原因：

- 同名字体的不同版本，字形 advance 可能有轻微差异。
- 对项目核心结论而言，这个差异通常不大；但如果你追求和当前 build 一致的逐字符容量，还是以**游戏内提取字体**为准更稳。

---

## 7. 断行规则资源：TMP 的 Leading / Following Characters

在 `resources.assets` 里确认到了 TMP 的行断规则文本资源：

- `LineBreaking Leading Characters`
- `LineBreaking Following Characters`

### 7.1 Leading Characters

```text
([｛〔〈《「『【〘〖〝‘“｟«$—…‥〳〴〵\［（{£¥"々〇〉》」＄｠￥￦ #
```

### 7.2 Following Characters

```text
)]｝〕〉》」』】〙〗〟’”｠»ヽヾーァィゥェォッャュョヮヵヶぁぃぅぇぉっゃゅょゎゕゖㇰㇱㇲㇳㇴㇵㇶㇷㇸㇹㇺㇻㇼㇽㇾㇿ々〻‐゠–〜?!‼⁇⁈⁉・、%,.:;。！？］）：；＝}¢°"†‡℃〆％，．
```

### 7.3 对实现的意义

这两张表比“手写几条中文标点规则”更权威，因为它们就是 TMP Settings 里实际带着的行断字符表。

项目实现时可直接采用：

- `Leading Characters`：尽量不要让这些字符出现在**行尾**。
- `Following Characters`：尽量不要让这些字符出现在**行首**。

这可以直接解释你之前对：

- `，。！？：；、`
- 各类括号
- 引号
- 日文小假名 / 长音符 / 迭代符

等字符的观察。

---

## 8. 从真实参数推出：为什么是 `280 × 245`、为什么是 9 行

### 8.1 可用宽度

`ChatText` 外框宽度为 `300`，左右 margin 为 `10 + 10`。

因此：

- 外框宽度：`300 px`
- 可用宽度：`300 - 10 - 10 = 280 px`

这一步非常关键。

**之前的 `x≈29/行` 不应再作为主基准。真正的主基准是 `usable_width = 280 px`。**

### 8.2 可用高度

`ChatText` 外框高度为 `265`，上下 margin 为 `10 + 10`。

因此：

- 外框高度：`265 px`
- 可用高度：`265 - 10 - 10 = 245 px`

### 8.3 为什么最多 9 行

从原始字体文件度量可得：

- `NotoSans-Regular` 行高单位：`1362 / 1000 em`
- `fontSize = 18`
- 拉丁行高约：`24.516 px`

于是：

- `245 / 24.516 ≈ 9.99`
- 取整后是 `9`

对于 `NotoSansCJK-JP-Regular`：

- 行高约：`26.064 px`
- `245 / 26.064 ≈ 9.39`
- 同样取整为 `9`

结论：**无论按拉丁主字体还是 CJK fallback 的行高看，最大可见行数都是 9。**

因此，`9 行` 不再只是黑盒经验值，而是可以由**文本框高度 + fontSize + 字体行高**直接解释。

---

## 9. 从真实参数推出：为什么 `x≈29/行`、`中≈15/行`

### 9.1 `x`

`NotoSans-Regular` 中：

- `unitsPerEm = 1000`
- `advance('x') = 529`

在 `fontSize = 18` 下：

- `x` 的像素宽度 ≈ `18 × 529 / 1000 = 9.522 px`

再用可用宽度 `280 px` 去除：

- `280 / 9.522 ≈ 29.40`
- 取整：`29`

### 9.2 `中`

`NotoSansCJK-JP-Regular` 中：

- `advance('中') = 1000`

在 `fontSize = 18` 下：

- `中` 的像素宽度 = `18 px`

再用可用宽度 `280 px` 去除：

- `280 / 18 ≈ 15.55`
- 取整：`15`

### 9.3 `.` `,` `:` `；` 这类窄 ASCII 标点

`NotoSans-Regular` 中：

- `advance('.') = 268`

于是：

- `18 × 268 / 1000 = 4.824 px`
- `280 / 4.824 ≈ 58.04`
- 取整：`58`

这和你之前对 `.` / `,` / `:` 的观察一致。

### 9.4 几个关键样本

| 字符 | 字体来源 | 预测单行容量 |
|---|---|---:|
| `x` | `NotoSans-Regular` | `29` |
| `a` | `NotoSans-Regular` | `27` |
| `b` | `NotoSans-Regular` | `25` |
| `c` | `NotoSans-Regular` | `32` |
| `f` | `NotoSans-Regular` | `45` |
| `i` | `NotoSans-Regular` | `60` |
| `m` | `NotoSans-Regular` | `16` |
| `w` | `NotoSans-Regular` | `19` |
| `W` | `NotoSans-Regular` | `16` |
| `A` | `NotoSans-Regular` | `24` |
| `H` | `NotoSans-Regular` | `20` |
| `中` | `NotoSansCJK-JP-Regular` | `15` |
| `，` | `NotoSansCJK-JP-Regular` | `15` |
| `。` | `NotoSansCJK-JP-Regular` | `15` |
| `.` | `NotoSans-Regular` | `58` |

---

## 10. 与你的实验的对应关系

### 10.1 单字符锚点

目前最可靠的单字符锚点（你已有截图或明确统计）可以被这套模型直接解释：

| 字符 | 实测 | 预测 | 结果 |
|---|---:|---:|---|
| `中` | `15` | `15` | 一致 |
| `x` | `29` | `29` | 一致 |
| `X` | `26` | `26` | 一致 |
| `1` | `27` | `27` | 一致 |
| `m` | `16` | `16` | 一致 |
| `w` | `19` | `19` | 一致 |
| `W` | `16` | `16` | 一致 |
| `0` | `27` | `27` | 一致 |
| `.` | `58` | `58` | 一致 |
| `:` | `58` | `58` | 一致 |
| `，` | `15` | `15` | 一致 |

### 10.2 你补发的 `a..z` / `A..H`

你后面补发的 34 个单字符容量值，与当前模型对比结果是：

- **34 / 34 完全一致**

### 10.3 纯中文 135 可见字符

你之前观察到：

- `中 × 144` 最终只显示 `135`

这现在可以直接解释成：

- 单行 `15`
- 最大 `9` 行
- `15 × 9 = 135`

这条链路已经闭合。

---

## 11. 现在最推荐的实现思路

### 11.1 先明确项目目标

如果你的目标是：

- 预测长文本会如何换行
- 在发送前做裁切
- 模拟 VRChat ChatBox 的 9 行限制

那么你真正要实现的是：

**“固定文本区域内的文本布局与裁切”**，而不是“整个聊天气泡 UI 的完整世界空间渲染”。

短文本时气泡背景可能会随内容变化；但对项目核心最重要的是：**长文本达到最大宽度后的换行与裁切行为。**

### 11.2 快速可用版（适合先落地）

如果你只需要一个强实用版本：

1. 使用 `usable_width = 280 px`
2. 使用 `max_lines = 9`
3. 字号固定 `18`
4. Latin 用 `NotoSans-Regular`
5. CJK 用 `NotoSansCJK-JP-Regular`
6. 其它脚本按 fallback 链挑选首个支持该字符的字体
7. 逐字符或逐 grapheme cluster 累加宽度
8. 优先在合法断点处换行
9. 超过 9 行后裁掉

这一版已经远强于“按固定字符数裁切”。

### 11.3 高保真版（推荐）

如果希望更稳，尤其是支持阿拉伯文、天城文、泰文等复杂脚本：

1. 按 **grapheme cluster** 而不是单个 codepoint 处理文本
2. 对每一段 run 选定实际字体（主字体或 fallback）
3. 使用 **HarfBuzz** 或等价 shaping 引擎得到 glyph advances
4. 累加 xAdvance
5. 用 Unicode line break + TMP leading/following 表决定可断点
6. 若超宽，回退到最近合法断点换行
7. 没有合法断点时，在 cluster 边界硬断
8. 总行数超过 9 行后裁切

这是最接近真实 TMP 行为的方案。

---

## 12. 可直接交给开发 / AI 的伪代码

```python
CONFIG = {
    "rect_width": 300,
    "rect_height": 265,
    "margin_left": 10,
    "margin_top": 10,
    "margin_right": 10,
    "margin_bottom": 10,
    "usable_width": 280,
    "usable_height": 245,
    "font_size": 18,
    "max_lines": 9,
    "primary_font": "NotoSans-Regular",
    "fallback_fonts": [
        "VRCCustom",
        "NotoEmoji-Regular",
        "NotoSansCJK-JP-Regular",
        "NotoSansHebrew-Regular",
        "NotoSansArabic-Regular",
        "NotoSansThai-Regular",
        "NotoSansArmenian-Regular",
        "NotoSansBengali-Regular",
        "NotoSansDevanagari-Medium",
        "NotoSansGeorgian-Regular",
        "NotoSansGujarati-Regular",
        "NotoSansGurmukhi-Regular",
        "NotoSansKannada-Regular",
        "NotoSansLao-Regular",
        "NotoSansMalayalam-Regular",
        "NotoSansOriya-Regular",
        "NotoSansTamil-Regular",
        "NotoSansTelugu-Regular",
        "NotoSansTibetanV-Regular",
    ],
}


def wrap_vrchat_chatbox(text):
    clusters = segment_into_grapheme_clusters(text)
    lines = []
    current_line = []
    current_width = 0
    last_break_index = None
    last_break_width = None

    for cluster in clusters:
        font = choose_first_supporting_font(cluster, CONFIG)
        shaped = shape_cluster_or_run(cluster, font, font_size=18)
        width = shaped.advance_x

        if is_break_opportunity(cluster, current_line):
            last_break_index = len(current_line)
            last_break_width = current_width

        if current_width + width <= CONFIG["usable_width"]:
            current_line.append(cluster)
            current_width += width
            continue

        if last_break_index is not None:
            line = current_line[:last_break_index]
            line = drop_trailing_break_space_if_needed(line)
            lines.append(join_clusters(line))

            remaining = current_line[last_break_index:]
            remaining = drop_leading_break_space_if_needed(remaining)
            current_line = remaining + [cluster]
            current_width = measure_clusters(current_line)
            last_break_index = None
            last_break_width = None
        else:
            if current_line:
                lines.append(join_clusters(current_line))
                current_line = [cluster]
                current_width = width
            else:
                # 单个 cluster 自身就超宽
                head, tail = hard_break_cluster(cluster, CONFIG["usable_width"], font)
                lines.append(head)
                current_line = [tail] if tail else []
                current_width = measure_clusters(current_line)

        if len(lines) >= CONFIG["max_lines"]:
            return lines[:CONFIG["max_lines"]]

    if current_line and len(lines) < CONFIG["max_lines"]:
        lines.append(join_clusters(current_line))

    return lines[:CONFIG["max_lines"]]
```

实现时请额外叠加 TMP 的两个规则：

- `Leading Characters` 不宜出现在行尾
- `Following Characters` 不宜出现在行首

这一步对中日文标点尤为重要。

---

## 13. 推荐的断行行为解释

下面是目前最合理、也是最适合直接写进项目里的行为解释。

### 13.1 空格

基于你的黑盒观察，空格应视为：

- 正常占宽
- 同时也是断点
- 如果实际在空格处断行，则新行不显示这个空格

这和常见排版行为一致，也符合你之前的实验现象。

### 13.2 CJK 连续文本

纯中文 / CJK 连续文本可以按字断。

但要叠加 TMP 的 `Following Characters` 限制；例如：

- `，。！？：；、` 等不应落到新行开头

因此实现上不要把 CJK 简化成“任何两个字符之间都能断”；应改成：

- **默认可断**
- **遇到 TMP 禁行首 / 禁行尾字符时禁止该断点**

### 13.3 ASCII 标点

ASCII 标点在 `NotoSans-Regular` 中通常比较窄，例如 `.` `,` `:` `;` 都是 `58/行` 级别。它们不应该被套用 CJK 宽字符规则。

### 13.4 复杂脚本

对于以下脚本，不建议只用“逐 codepoint 查 advance”来做：

- 阿拉伯文
- 天城文
- 孟加拉文
- 泰文
- 其它依赖 shaping / 重排 / 连写的脚本

对这些脚本，应该使用 shaping 后的 glyph advance。

---

## 14. 什么已经足够确定，什么还不能说死

### 14.1 已经足够确定，可直接用于项目

下面这些项已经足够拿来做项目实现：

- ChatBox 文本对象路径
- 主字体资产 `NotoSans-Regular SDF`
- 原始主字体 `NotoSans-Regular`
- CJK 主要 fallback `NotoSansCJK-JP-Regular`
- `ChatText` 的 `fontSize = 18`
- `characterSpacing = 0`
- `wordSpacing = 0`
- `lineSpacing = 0`
- `margin = (10,10,10,10)`
- `enableAutoSizing = false`
- `RectTransform = 300 × 265`
- 实际可用区域 `280 × 245`
- 最大可见行数 `9`
- TMP 的 Leading / Following Characters 资源
- “按字体度量即可解释单字符容量”的事实

### 14.2 仍然保留不确定性，但不影响主项目

下面这些点仍应保持谨慎：

1. **完整自定义 MonoBehaviour typetree** 还没有拿到。
   - 原因是 `global-metadata.dat` 仍受保护，无法直接走常规 typetree 生成。

2. **ChatBubble 背景如何随短文本变化** 还没有完全拆穿。
   - 在相关对象链上没有看到命名明确的 `ContentSizeFitter / LayoutElement / HorizontalLayoutGroup / VerticalLayoutGroup`。
   - 这说明气泡宽度变化很可能是由自定义脚本控制。
   - 但这不影响“长文本达到最大宽度后的换行与 9 行裁切”模型。

3. **个别非关键字段**（例如 normal/mirror 的 `overflowMode` 差异）不要作为项目主依据。
   - 它们可能是真差异，也可能与当前启发式字段定位有关。
   - 对聊天内容裁切来说，这不是关键项。

---

## 15. 对项目实施的最终建议

### 15.1 如果目标是“马上做出可用版本”

直接使用：

- `usable_width = 280`
- `usable_height = 245`
- `fontSize = 18`
- `max_lines = 9`
- `NotoSans-Regular` + `NotoSansCJK-JP-Regular`
- TMP Leading / Following line break tables

并实现一个：

- 按字形宽度累加
- 优先在合法断点回退
- 最后裁到 9 行

的换行器。

### 15.2 如果目标是“对多语言尽可能接近原游戏”

在上面的基础上再加：

- 完整 fallback 链
- HarfBuzz shaping
- grapheme cluster 级处理
- TMP line break 规则

这会比“按 codepoint 数量裁切”高一个量级。

### 15.3 如果目标是“继续逆向到更底层”

真正值得继续挖的只有两类：

- 运行时解密后的 metadata / typetree
- 控制气泡背景宽度变化的自定义脚本逻辑

但这已经超出了“完成 ChatBox 裁切项目”的最小必需范围。

---

## 16. 最终结论

这次研究的真正成果不是“找到一个字体名”，而是把 VRChat ChatBox 的长文本行为收敛成了一套**足够稳定、可复现、可实现**的模型：

- 文本框固定为 `300 × 265`
- 边距 `10,10,10,10`
- 有效排版区域 `280 × 245`
- 字号 `18`
- 不额外加字距 / 词距 / 行距
- 不自动缩放
- 主字体 `NotoSans-Regular`
- CJK 主要 fallback `NotoSansCJK-JP-Regular`
- 根据真实字形宽度换行
- 使用 TMP line-breaking 字符表处理标点断行
- 最多显示 `9` 行

因此，**你现在完全可以不再依赖“x 作为基准”或“手动一条条测字符”的旧方案，而直接改成“真实字体度量 + 真实 TMP 参数 + TMP 行断规则”的实现。**

这就是当前最适合交给别人、交给另一个 AI、或者直接进入项目开发的最终版本。

---

## 附录 A：关键 asset / object ID（便于复核）

### A.1 资源 ID

- `TMP Settings`：`resources.assets`, path id `107463`

### A.2 组件 / RectTransform ID

- 普通 `ChatText` 组件：`6792`
- 镜像 `ChatText` 组件：`6471`
- 普通 `ChatText` RectTransform：`5914`
- 镜像 `ChatText` RectTransform：`6022`
- 普通 `Chat` RectTransform：`5902`
- 普通 `Canvas` RectTransform：`6024`
- `ChatBubble` Transform：`4145`
- `ChatBubbleMirror` Transform：`4871`

这些 ID 主要用于复核，不是项目实现必须项。

---

## 附录 B

VRChat TMP line breaking resources extracted from resources.assets

TextAsset path 1767
name: LineBreaking Leading Characters
raw_repr: "\ufeff([｛〔〈《「『【〘〖〝‘“｟«$—…‥〳〴〵\\［（{£¥\"々〇〉》」＄｠￥￦ #"
without_bom: "([｛〔〈《「『【〘〖〝‘“｟«$—…‥〳〴〵\\［（{£¥\"々〇〉》」＄｠￥￦ #"
notes: includes one ASCII space before '#'

TextAsset path 1784
name: LineBreaking Following Characters
raw_repr: "\ufeff)]｝〕〉》」』】〙〗〟’”｠»ヽヾーァィゥェォッャュョヮヵヶぁぃぅぇぉっゃゅょゎゕゖㇰㇱㇲㇳㇴㇵㇶㇷㇸㇹㇺㇻㇼㇽㇾㇿ々〻‐゠–〜?!‼⁇⁈⁉・、%,.:;。！？］）：；＝}¢°\"†‡℃〆％，．"
without_bom: ")]｝〕〉》」』】〙〗〟’”｠»ヽヾーァィゥェォッャュョヮヵヶぁぃぅぇぉっゃゅょゎゕゖㇰㇱㇲㇳㇴㇵㇶㇷㇸㇹㇺㇻㇼㇽㇾㇿ々〻‐゠–〜?!‼⁇⁈⁉・、%,.:;。！？］）：；＝}¢°\"†‡℃〆％，．"
