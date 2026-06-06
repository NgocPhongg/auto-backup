import re


_GOLOGIN_PROFILE_ID_RE = re.compile(r"^[0-9a-fA-F]{24}$")


def is_real_gologin_profile_id(value):
    text = str(value or "").strip()
    return bool(_GOLOGIN_PROFILE_ID_RE.fullmatch(text))


def first_real_gologin_profile_id(*values):
    for value in values:
        text = str(value or "").strip()
        if is_real_gologin_profile_id(text):
            return text
    return ""
