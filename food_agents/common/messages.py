def describe_issues(issues: list[dict]) -> str:
    """Turn the issues list from quote_items into one readable sentence."""
    parts = []
    for i in issues:
        if i["problem"] == "unavailable":
            alt = ", ".join(i["alternatives"]) or "none"
            parts.append(f"{i['item']} is unavailable (alternatives: {alt})")
        elif i["problem"] == "ambiguous":
            parts.append(f"'{i['item']}' matches several dishes: {', '.join(i['options'])}")
        else:
            parts.append(f"'{i['item']}' is not on the menu")
    return "; ".join(parts)