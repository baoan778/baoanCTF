"""termcolor 最小替身 —— jwt_tool 只用它给输出上色，不影响逻辑。
"""
import sys

ATTRIBUTES = {"bold": 1, "dark": 2, "underline": 4, "blink": 5, "reverse": 7, "concealed": 8}
COLORS = {
    "grey": 30, "red": 31, "green": 32, "yellow": 33, "blue": 34,
    "magenta": 35, "cyan": 36, "white": 37,
    "light_grey": 90, "light_red": 91, "light_green": 92, "light_yellow": 93,
    "light_blue": 94, "light_magenta": 95, "light_cyan": 96,
}
HIGHLIGHTS = {
    "on_grey": 40, "on_red": 41, "on_green": 42, "on_yellow": 43, "on_blue": 44,
    "on_magenta": 45, "on_cyan": 46, "on_white": 47,
}


def colored(text, color=None, on_color=None, attrs=None):
    if not sys.stdout.isatty():
        return text
    codes = []
    if color in COLORS:
        codes.append(str(COLORS[color]))
    if on_color in HIGHLIGHTS:
        codes.append(str(HIGHLIGHTS[on_color]))
    for a in attrs or []:
        if a in ATTRIBUTES:
            codes.append(str(ATTRIBUTES[a]))
    if not codes:
        return text
    return "\033[" + ";".join(codes) + "m" + str(text) + "\033[0m"


def cprint(text, color=None, on_color=None, attrs=None, **kwargs):
    print(colored(text, color, on_color, attrs), **kwargs)