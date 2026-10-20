import uuid


def _prefixed_id(prefix: str, length: int) -> str:
    return f"{prefix}{uuid.uuid4().hex[:length]}"


def generate_applicant_id() -> str:
    return str(uuid.uuid4())


def generate_policy_id() -> str:
    return _prefixed_id("pol_", 16)
