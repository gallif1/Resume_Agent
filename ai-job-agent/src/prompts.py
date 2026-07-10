"""Interactive CLI prompts."""


def ask_yes_no(message: str, *, default: bool = False) -> bool:
    """Ask the user to confirm. Accepts y/yes/n/no (Hebrew and English)."""
    suffix = " [Y/n]" if default else " [y/N]"
    print(message)
    try:
        answer = input(suffix + " ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    if not answer:
        return default

    if answer in ("y", "yes", "כ", "כן"):
        return True
    if answer in ("n", "no", "ל", "לא"):
        return False

    print("Unrecognized answer - stopping.")
    return False
