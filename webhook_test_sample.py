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
