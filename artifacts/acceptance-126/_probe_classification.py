import sys

sys.path.insert(0, ".")
from app.tools.local.plan import looks_like_autonomous, looks_like_file_task, looks_like_write

T4L = (
    r"В папке C:\Users\Volkr\Canalla E2E\T4L открой t4l.txt, сохрани его содержимое в "
    r"t4l_out.txt, затем открой t4l_out.txt и подтверди содержимое. В конце выведи T4L_DONE."
)
T3L = (
    r"В папке C:\Users\Volkr\Canalla E2E\T3L открой t3l.txt и покажи его содержимое. "
    r"В конце выведи T3L_DONE."
)
CASES = [
    ("T4L", T4L),
    ("T3L", T3L),
    ("question", "Как открыть main.py в редакторе и что такое вообще точка входа?"),
    ("question2", "Что такое файл .gitignore?"),
    ("question3", "Расскажи про index.html и его структуру"),
    ("write", "сохрани его содержимое в t4l_out.txt"),
    ("plain", "Привет! Как дела?"),
]
for name, text in CASES:
    print(
        f"{name:10} file_task={looks_like_file_task(text)!s:5} "
        f"autonomous={looks_like_autonomous(text)!s:5} write={looks_like_write(text)}"
    )
