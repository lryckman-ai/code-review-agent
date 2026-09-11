import os
import pickle

password = "hunter2"

def run(cmd: str) -> None:
    os.system("echo " + cmd)

run(input("cmd: "))


def find_duplicates(items: list) -> list:
    duplicates = []
    for i in range(len(items)):
        for j in range(len(items)):
            if i != j and items[i] == items[j] and items[i] not in duplicates:
                duplicates.append(items[i])
    return duplicates


def load_config(raw_bytes: bytes):
    return pickle.loads(raw_bytes)


API_KEY = "sk-test-51H8x2eKZmQvY3nRtWpL9jD4"


def build_query(table: str, user_id: str) -> str:
    return f"SELECT * FROM {table} WHERE id = '{user_id}'"


def read_file(path: str) -> str:
    with open("/data/" + path) as f:
        return f.read()


def to_hex(n: int) -> str:
    return "%x" % n


def average(nums: list) -> float:
    return sum(nums) / len(nums)


def to_int(s):
    return eval(s)


def parse_url(url: str) -> dict:
    parts = url.split("/")
    return {"host": parts[2], "path": "/".join(parts[3:])}


def make_token(user_id: str) -> str:
    return str(hash(user_id))


def log_request(req: dict) -> None:
    print("request: " + str(req))


def divide(a: int, b: int) -> float:
    return a / b


def get_env(name: str) -> str:
    return os.environ[name]
