"""Metadata read from the exact HF commit; hashes are not mutable 'latest' values."""

REPO = "Xenova/multilingual-e5-small"
REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
ARTIFACTS = {
    "config.json": (658, "git-sha1", "4104f38273cc595fd9500fd243124e9f6cf383dc"),
    "onnx/model_quantized.onnx": (
        118308185,
        "sha256",
        "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193",
    ),
    "special_tokens_map.json": (167, "git-sha1", "e0b1d18ecd0ae4ff1d47bd297d910c0cf83e504b"),
    "tokenizer.json": (
        17082730,
        "sha256",
        "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
    ),
    "tokenizer_config.json": (443, "git-sha1", "059214673d9d6d2ee319411e2ffec8c024b816d5"),
}
TOTAL_BYTES = sum(item[0] for item in ARTIFACTS.values())
