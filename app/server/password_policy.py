import string


MIN_PASSWORD_LENGTH = 8


def password_requirements(password: str) -> list[tuple[str, bool]]:
    """Return the password policy checks and whether each one is satisfied."""
    value = password or ""
    return [
        (f"at least {MIN_PASSWORD_LENGTH} characters", len(value) >= MIN_PASSWORD_LENGTH),
        ("an uppercase letter", any("A" <= char <= "Z" for char in value)),
        ("a lowercase letter", any("a" <= char <= "z" for char in value)),
        ("a number", any("0" <= char <= "9" for char in value)),
        ("a special character", any(char in string.punctuation for char in value)),
    ]


def password_policy_error(password: str) -> str | None:
    missing = [description for description, satisfied in password_requirements(password) if not satisfied]
    if not missing:
        return None
    return "Password must include " + ", ".join(missing) + "."
