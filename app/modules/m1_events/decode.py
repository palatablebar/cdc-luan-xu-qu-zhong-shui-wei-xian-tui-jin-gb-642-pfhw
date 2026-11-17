"""字节层：编码回退链与 BOM 处理。

导出端的声明不可信：先按声明试一次，失败按固定顺序回退（utf-8 → gb18030）。
回退成功但实际编码与声明不符时记 E_CHARSET_MISMATCH，不静默接受。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ... import config


@dataclass
class Decoded:
    text: str
    encoding: str
    declared: str | None
    bom_stripped: bool
    codes: list[str] = field(default_factory=list)
    failed: bool = False


def normalize_charset(name: str | None) -> str | None:
    if not name:
        return None
    key = name.strip().strip("'\"").lower()
    return config.CHARSET_ALIASES.get(key, key)


def chain_for(declared: str | None) -> list[str]:
    """声明编码排在首位，其余按固定顺序回退；顺序固定保证结果可复算。"""
    chain = list(config.ENCODING_CHAIN)
    declared = normalize_charset(declared)
    if declared and declared in chain:
        chain.remove(declared)
        chain.insert(0, declared)
    elif declared:
        chain.insert(0, declared)
    return chain


def decode_bytes(raw: bytes, *, declared: str | None = None) -> Decoded:
    """TRAP-01：声明 utf-8 但字节是 GB18030 时，必须回退且给出原因码。"""
    declared_norm = normalize_charset(declared)
    bom = raw.startswith(b"\xef\xbb\xbf")
    for encoding in chain_for(declared):
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        codes: list[str] = []
        if bom:
            codes.append("S_BOM_STRIPPED")
        text = text.lstrip("\ufeff")
        if declared_norm and declared_norm != encoding:
            codes.append("E_CHARSET_MISMATCH")
        return Decoded(text=text, encoding=encoding, declared=declared_norm,
                       bom_stripped=bom, codes=codes)
    return Decoded(text="", encoding="", declared=declared_norm, bom_stripped=bom,
                   codes=["E_DECODE_FAILED"], failed=True)


def is_blank(text: str) -> bool:
    return not text.strip()
