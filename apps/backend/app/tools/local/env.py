SANITIZED_ENV = (
    "SystemRoot",
    "SystemDrive",
    "windir",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "OS",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
)
BLOCKED_ENV = (
    "RUNPOD_API_KEY",
    "TINYFISH_API_KEY",
    "LLM_API_KEY",
    "JWT_SECRET",
    "ALEX_DEVICE_CREDENTIAL",
    "OPENAI_API_KEY",
)


def sanitized_environment(source=None):
    import os

    env = source if source is not None else os.environ
    result = {}
    for key in SANITIZED_ENV:
        if env.get(key):
            result[key] = env[key]
    path = env.get("PATH", "")
    roots = [env.get("SystemRoot", r"C:\Windows")]
    allowed = []
    for part in path.split(";"):
        lowered = part.casefold()
        if any(part and lowered.startswith(root.casefold()) for root in roots if root):
            allowed.append(part)
    result["PATH"] = ";".join(allowed) if allowed else roots[0] + r"\system32"
    for blocked in BLOCKED_ENV:
        result.pop(blocked, None)
    return result
