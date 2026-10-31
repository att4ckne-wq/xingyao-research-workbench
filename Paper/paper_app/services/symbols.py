"""实验记录 - 科学文本符号工具：Unicode 上下标映射与分类符号集。

上下标采用 Unicode 字符直接插入，保证纯文本存储、FTS 全文检索与
Markdown 导出全链路可用；复杂公式使用 LaTeX（matplotlib mathtext 语法）。
"""

from __future__ import annotations

# 上标映射（q 无 Unicode 上标，保持原字符）
SUPERSCRIPT_MAP: dict[str, str] = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽", ")": "⁾",
    "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ", "f": "ᶠ", "g": "ᵍ", "h": "ʰ",
    "i": "ⁱ", "j": "ʲ", "k": "ᵏ", "l": "ˡ", "m": "ᵐ", "n": "ⁿ", "o": "ᵒ", "p": "ᵖ",
    "r": "ʳ", "s": "ˢ", "t": "ᵗ", "u": "ᵘ", "v": "ᵛ", "w": "ʷ", "x": "ˣ", "y": "ʸ", "z": "ᶻ",
}

# 下标映射（b/c/d/f/g/q/w/y 无常规 Unicode 下标，保持原字符）
SUBSCRIPT_MAP: dict[str, str] = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "+": "₊", "-": "₋", "=": "₌", "(": "₍", ")": "₎",
    "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ", "l": "ₗ", "m": "ₘ",
    "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ", "s": "ₛ", "t": "ₜ", "u": "ᵤ", "v": "ᵥ", "x": "ₓ",
}


def convert_to_superscript(text: str, letters: bool = True) -> str:
    """letters=False 用于选中转换：只转数字与数学符号，避免把普通单词里的字母转掉。"""
    output = []
    for ch in text:
        mapped = SUPERSCRIPT_MAP.get(ch)
        if mapped is None or (ch.isalpha() and not letters):
            output.append(ch)
        else:
            output.append(mapped)
    return "".join(output)


def convert_to_subscript(text: str, letters: bool = True) -> str:
    output = []
    for ch in text:
        mapped = SUBSCRIPT_MAP.get(ch)
        if mapped is None or (ch.isalpha() and not letters):
            output.append(ch)
        else:
            output.append(mapped)
    return "".join(output)


GREEK_LETTERS: list[str] = list("αβγδεζηθικλμνξοπρστυφχψω") + list("ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ")

OPERATORS: list[str] = list("×÷±≤≥≠≈∝∞√∑∏∫Δ∂°·⊥∥∈⊆∪∩")

UNITS: list[str] = list("℃℉µΩÅ‰%′″")

ARROWS: list[str] = list("→←↔⇒⇌↑↓")

SYMBOL_TABS: tuple[tuple[str, list[str]], ...] = (
    ("希腊字母", GREEK_LETTERS),
    ("运算符", OPERATORS),
    ("单位", UNITS),
    ("箭头", ARROWS),
)
