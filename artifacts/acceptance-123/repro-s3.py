"""Free reproduction of the S3 argument coercion (no compute, no model)."""

from app.tools.local.targets import coerce_file_argument

PATH = r"C:\Users\Volkr\Canalla Acceptance\S3\readme with spaces.txt"
PROMPT = (
    "Открой файл " + PATH + " через инструмент read_file и перескажи его содержимое "
    "одним предложением."
)

for offered in (PATH, '"' + PATH + '"', "readme with spaces.txt", r"S3\readme with spaces.txt"):
    try:
        print(repr(offered), "->", repr(coerce_file_argument("read_file", offered, PROMPT)))
    except Exception as error:  # noqa: BLE001 - this script reports, never hides
        print(repr(offered), "-> ERROR", type(error).__name__, getattr(error, "code", str(error)))
