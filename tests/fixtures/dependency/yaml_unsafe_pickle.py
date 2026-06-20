"""Config and model loader — multiple dependency vulnerabilities."""
import hashlib
import pickle
import random
import yaml


def load_config(path: str) -> dict:
    with open(path) as f:
        # VULN: yaml.load without Loader allows arbitrary code execution
        return yaml.load(f)


def load_ml_model(model_path: str):
    with open(model_path, "rb") as f:
        # VULN: pickle.load on an untrusted/filesystem file
        return pickle.load(f)


def save_session(session_data: dict, path: str) -> None:
    with open(path, "wb") as f:
        # VULN: session serialized with pickle — if path is user-controlled,
        # an attacker can swap the file before it's read back
        pickle.dump(session_data, f)


def hash_password(password: str) -> str:
    # VULN: MD5 is cryptographically broken for password storage
    return hashlib.md5(password.encode()).hexdigest()


def verify_password(password: str, stored_hash: str) -> bool:
    # VULN: MD5 + no constant-time comparison
    return hashlib.md5(password.encode()).hexdigest() == stored_hash


def generate_otp() -> str:
    # VULN: random is not cryptographically secure — predictable seed
    return str(random.randint(100_000, 999_999))


def compute_file_checksum(path: str) -> str:
    # VULN: SHA1 is collision-prone for integrity checks
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()
