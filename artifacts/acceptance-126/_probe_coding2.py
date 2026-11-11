import sys

sys.path.insert(0, ".")
from app.tools.local.workspace import looks_like_coding

root = r"C:\Users\Volkr\AppData\Local\Temp\pytest-of-Volkr\pytest-12\test_x0\Canalla E2E\T4L"
prompt = (
    f"В папке {root} открой t4l.txt, сохрани его содержимое в t4l_out.txt, "
    "затем открой t4l_out.txt и подтверди содержимое. В конце выведи T4L_DONE."
)
print("coding", looks_like_coding(prompt))
print("prompt", prompt)
