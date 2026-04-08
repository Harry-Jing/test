"""Store the fixed VRChat chatbox model derived from the final report."""

from dataclasses import dataclass

MAX_CHATBOX_CHARS = 144
MAX_CHATBOX_LINES = 9
MAX_RECENT_CLOSED_UTTERANCES = 2048
PARTIAL_MIN_INTERVAL_SECONDS = 1.5
FINAL_SEND_GUARD_SECONDS = 0.3
TYPING_IDLE_TIMEOUT_SECONDS = 1.5

RECT_WIDTH_PX = 300.0
RECT_HEIGHT_PX = 265.0
MARGIN_LEFT_PX = 10.0
MARGIN_TOP_PX = 10.0
MARGIN_RIGHT_PX = 10.0
MARGIN_BOTTOM_PX = 10.0
USABLE_WIDTH_PX = RECT_WIDTH_PX - MARGIN_LEFT_PX - MARGIN_RIGHT_PX
USABLE_HEIGHT_PX = RECT_HEIGHT_PX - MARGIN_TOP_PX - MARGIN_BOTTOM_PX
FONT_SIZE_PX = 18.0

PRIMARY_FONT_NAME = "NotoSans-Regular"
EMOJI_FONT_NAME = "NotoEmoji-Regular"
CJK_PRIMARY_FONT_NAME = "NotoSansCJK-JP-Regular"

TMP_LEADING_CHARACTERS = (
    '([｛〔〈《「『【〘〖〝‘“｟«$—…‥〳〴〵\\［（{£¥"々〇〉》」＄｠￥￦ #'
)
TMP_FOLLOWING_CHARACTERS = (
    ")]｝〕〉》」』】〙〗〟’”｠»ヽヾーァィゥェォッャュョヮヵヶ"
    "ぁぃぅぇぉっゃゅょゎゕゖㇰㇱㇲㇳㇴㇵㇶㇷㇸㇹㇺㇻㇼㇽㇾㇿ々〻‐゠–〜?!‼⁇⁈⁉・、%,.:;。！？］）：；＝}¢°"
    '"†‡℃〆％，．'
)


@dataclass(frozen=True, slots=True)
class FontResourceSpec:
    """Describe one bundled font resource."""

    name: str
    file_name: str
    required: bool = False


FONT_RESOURCE_SPECS = (
    FontResourceSpec(PRIMARY_FONT_NAME, "NotoSans-Regular.ttf", required=True),
    FontResourceSpec(EMOJI_FONT_NAME, "NotoEmoji-Regular.ttf", required=True),
    FontResourceSpec(
        CJK_PRIMARY_FONT_NAME, "NotoSansCJK-JP-Regular.otf", required=True
    ),
    FontResourceSpec("NotoSansArabic-Regular", "NotoSansArabic-Regular.ttf"),
    FontResourceSpec("NotoSansArmenian-Regular", "NotoSansArmenian-Regular.ttf"),
    FontResourceSpec("NotoSansBengali-Regular", "NotoSansBengali-Regular.ttf"),
    FontResourceSpec("NotoSansDevanagari-Medium", "NotoSansDevanagari-Medium.ttf"),
    FontResourceSpec("NotoSansGeorgian-Regular", "NotoSansGeorgian-Regular.ttf"),
    FontResourceSpec("NotoSansGujarati-Regular", "NotoSansGujarati-Regular.ttf"),
    FontResourceSpec("NotoSansGurmukhi-Regular", "NotoSansGurmukhi-Regular.ttf"),
    FontResourceSpec("NotoSansKannada-Regular", "NotoSansKannada-Regular.ttf"),
    FontResourceSpec("NotoSansLao-Regular", "NotoSansLao-Regular.ttf"),
    FontResourceSpec("NotoSansMalayalam-Regular", "NotoSansMalayalam-Regular.ttf"),
    FontResourceSpec("NotoSansOriya-Regular", "NotoSansOriya-Regular.ttf"),
    FontResourceSpec("NotoSansTamil-Regular", "NotoSansTamil-Regular.ttf"),
    FontResourceSpec("NotoSansTelugu-Regular", "NotoSansTelugu-Regular.ttf"),
    FontResourceSpec("NotoSansThai-Regular", "NotoSansThai-Regular.ttf"),
    FontResourceSpec("NotoSansTibetanV-Regular", "NotoSansTibetanV-Regular.ttf"),
)

PRIMARY_SENTENCE_TERMINATORS = frozenset("。！？.!?")
SECONDARY_BREAKPOINTS = frozenset("，、；：,;:")
CLOSING_PUNCTUATION = frozenset("\"')]}>”’）】》」』")
ASCII_SENTENCE_SEPARATORS = frozenset(".!?,;:")
