import sys

sys.path.insert(0, ".")
from app.tools.local.workspace import looks_like_coding

T4L = (
    r"В папке C:\work\T4L открой t4l.txt, сохрани его содержимое в t4l_out.txt, "
    r"затем открой t4l_out.txt и подтверди содержимое. В конце выведи T4L_DONE."
)
cases = [
    ("T4L", T4L),
    ("T3L", "В папке X собери два небольших скрипта: t3l_a.py с печатью T3L_A"),
    ("question", "Как открыть main.py в редакторе?"),
]
for name, text in cases:
    print(f"{name:9} coding={looks_like_coding(text)}")
