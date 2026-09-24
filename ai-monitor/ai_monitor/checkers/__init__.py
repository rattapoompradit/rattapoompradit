from . import hermes, ollama, openai_compat, status_page

# type -> async check(provider, client) -> CheckResult
CHECKERS = {
    "status_page": status_page.check,
    "openai_compat": openai_compat.check,
    "ollama": ollama.check,
    "hermes": hermes.check,
}
