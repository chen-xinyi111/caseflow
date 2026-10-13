def count_text(text: str) -> dict[str, int]:
    return {"char_count": len(text), "word_count": len(text.split())}
